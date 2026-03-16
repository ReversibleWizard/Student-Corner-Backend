"""
InterviewerAgent — central orchestrator.
Owns three sub-agents and all session logic.
Caches the final InterviewSummary object for middleware dispatch.
"""
from __future__ import annotations

import asyncio
from agents import Agent, Runner, trace

from ai_interviewer.models import (
    AnswerReview, NextQuestion, InterviewSummary,
    InterviewContext, StartSessionRequest,
    InterviewerReply, AnswerReviewResult, NextQuestionResult, SummaryResult,
)
from ai_interviewer.exceptions import (
    AgentError, AgentTimeoutError, AgentOutputError,
    SessionAlreadyCompletedError, InvalidInputError,
)
from ai_interviewer.logger import get_logger

log = get_logger(__name__)

MODEL           = "gpt-4o-mini"
AGENT_TIMEOUT_S = 60


class InterviewerAgent:
    """
    Orchestrates the full interview session:
      - Builds AnswerReviewAgent, QuestionGeneratorAgent, SummaryAgent
      - Routes answers: review → next question or summary
      - Caches last InterviewSummary in self.last_summary for middleware use
    """

    def __init__(self, context: InterviewContext, req: StartSessionRequest, resume: str = ""):
        self.ctx          = context
        self.req          = req
        self.last_summary: InterviewSummary | None = None   # populated when session ends

        self._review_agent   = self._build_review_agent()
        self._question_agent = self._build_question_agent(resume)
        self._summary_agent  = self._build_summary_agent()

        log.info(
            "InterviewerAgent ready — candidate='%s' role='%s' max_q=%d",
            req.name, req.target_role, req.max_questions,
        )

    # ── Agent builders ────────────────────────────────────────────────────────

    def _build_review_agent(self) -> Agent:
        return Agent(
            name="AnswerReviewAgent",
            model=MODEL,
            output_type=AnswerReview,
            instructions=f"""
You are an expert technical interviewer evaluating a candidate's answer.

Candidate profile:
- Name: {self.req.name}
- Experience: {self.req.experience_level} ({self.req.work_experience})
- Confidence: {self.req.confidence_level}
- Target role: {self.req.target_role}

Score 0-10 (0-3 wrong, 4-5 partial, 6-7 good, 8-9 strong, 10 excellent).
Identify concrete strengths and weaknesses.
Actionable feedback, 3-6 sentences, not generic.
Label topic (e.g. 'Python', 'System Design') and difficulty ('easy'/'medium'/'hard').
Tone: professional but encouraging.
""",
        )

    def _build_question_agent(self, resume: str) -> Agent:
        return Agent(
            name="QuestionGeneratorAgent",
            model=MODEL,
            output_type=NextQuestion,
            instructions=f"""
You are a smart interview question generator.

Candidate:
- Name: {self.req.name}
- Experience: {self.req.experience_level}, {self.req.work_experience}
- Role: {self.req.target_role}
- Duration: {self.req.duration_minutes} min

Resume:
{resume or '(not provided)'}

Priority topics: {self.req.priority_topics}

Rules:
- NEVER repeat a question from history
- Score >= 7 → harder | 4-6 → same | <= 3 → easier or new topic
- Prioritise uncovered topics
- If few questions remain, prefer behavioural questions
- Ground questions in the candidate's resume
- One short specific question only
""",
        )

    def _build_summary_agent(self) -> Agent:
        return Agent(
            name="SummaryAgent",
            model=MODEL,
            output_type=InterviewSummary,
            instructions=f"""
Generate a post-interview performance report.

Candidate: {self.req.name}
Role: {self.req.target_role}
Experience: {self.req.experience_level}, {self.req.work_experience}

Compute average score, identify strong/weak topics, give a hiring
recommendation ('Strong Yes'/'Yes'/'Maybe'/'No'), and write a detailed
narrative covering technical depth, communication, growth areas, and
overall suitability. Be honest, specific, and fair.
""",
        )

    # ── Agent runner with timeout + error handling ────────────────────────────

    async def _run_agent(self, agent: Agent, prompt: str, agent_name: str):
        try:
            with trace(agent_name):
                result = await asyncio.wait_for(
                    Runner.run(agent, prompt),
                    timeout=AGENT_TIMEOUT_S,
                )
            if result.final_output is None:
                raise AgentOutputError(agent_name=agent_name, reason="final_output is None")
            return result.final_output

        except asyncio.TimeoutError:
            log.error("Agent '%s' timed out after %ds", agent_name, AGENT_TIMEOUT_S)
            raise AgentTimeoutError(agent_name=agent_name, timeout_seconds=AGENT_TIMEOUT_S)
        except (AgentTimeoutError, AgentOutputError):
            raise
        except Exception as exc:
            log.error("Agent '%s' error: %s", agent_name, exc)
            raise AgentError(message=f"Agent '{agent_name}' failed.", detail=str(exc)) from exc

    # ── Internal steps ────────────────────────────────────────────────────────

    async def _review_answer(self, question: str, answer: str) -> AnswerReview:
        return await self._run_agent(
            self._review_agent,
            f"Question asked: {question}\n\nCandidate answer: {answer}",
            "AnswerReviewAgent",
        )

    async def _next_question(self, review: AnswerReview) -> NextQuestion:
        history_lines = "\n".join(
            f"  Q{i+1} [{h.difficulty}][{h.topic_covered}] {h.score}/10 — {h.question}"
            for i, h in enumerate(self.ctx.history)
        )
        prompt = f"""
Interview history:
{history_lines or '  (none yet)'}

Last answer:
  Question: {review.question}
  Score: {review.score}/10  Difficulty: {review.difficulty}  Topic: {review.topic_covered}
  Weaknesses: {review.weaknesses}

Topics covered: {", ".join(self.ctx.topics_covered) or "None yet"}
Questions remaining: {self.req.max_questions - self.ctx.question_count}

Generate the best next question.
"""
        return await self._run_agent(self._question_agent, prompt, "QuestionGeneratorAgent")

    async def _generate_summary(self) -> SummaryResult:
        if not self.ctx.history:
            return SummaryResult(
                overall_score=0, score_bar="░" * 10,
                total_questions=0, strong_topics="N/A", weak_topics="N/A",
                hiring_recommendation="No",
                detailed_summary="No answers were recorded.",
            )

        history_text = "\n\n".join(
            f"Q{i+1}: {h.question}\n"
            f"Answer: {h.user_answer}\n"
            f"Score: {h.score}/10 | Topic: {h.topic_covered} | Difficulty: {h.difficulty}\n"
            f"Feedback: {h.user_answer_review}"
            for i, h in enumerate(self.ctx.history)
        )
        summary: InterviewSummary = await self._run_agent(
            self._summary_agent, history_text, "SummaryAgent"
        )

        # Cache raw summary for middleware dispatch
        self.last_summary = summary

        scores = [h.score for h in self.ctx.history]
        avg    = sum(scores) / len(scores)
        bar    = "█" * int(avg) + "░" * (10 - int(avg))

        return SummaryResult(
            overall_score         = round(avg, 1),
            score_bar             = bar,
            total_questions       = summary.total_questions,
            strong_topics         = summary.strong_topics,
            weak_topics           = summary.weak_topics,
            hiring_recommendation = summary.hiring_recommendation,
            detailed_summary      = summary.detailed_summary,
        )

    def _build_reply(
        self,
        review:  AnswerReview,
        next_q:  NextQuestion,
    ) -> InterviewerReply:
        bar = "█" * review.score + "░" * (10 - review.score)
        return InterviewerReply(
            is_completed  = False,
            review        = AnswerReviewResult(
                score      = review.score,
                score_bar  = bar,
                strengths  = review.strengths,
                weaknesses = review.weaknesses,
                feedback   = review.user_answer_review,
                topic      = review.topic_covered,
                difficulty = review.difficulty,
            ),
            next_question = NextQuestionResult(
                question_number = self.ctx.question_count,
                question        = next_q.question,
                topic           = next_q.topic,
                difficulty      = next_q.difficulty,
            ),
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def handle_response(self, user_message: str) -> InterviewerReply:
        """Process a candidate's answer. Returns next question or final summary."""
        if self.ctx.is_completed:
            raise SessionAlreadyCompletedError()

        if not user_message or not user_message.strip():
            raise InvalidInputError("Answer cannot be empty.")

        log.info(
            "handle_response q=%d/%d topic='%s'",
            self.ctx.question_count + 1, self.req.max_questions, self.ctx.current_topic,
        )

        review = await self._review_answer(self.ctx.current_question, user_message)

        self.ctx.history.append(review)
        self.ctx.question_count += 1
        if review.topic_covered not in self.ctx.topics_covered:
            self.ctx.topics_covered.append(review.topic_covered)

        log.info("Answer scored: %d/10 topic='%s'", review.score, review.topic_covered)

        if self.ctx.question_count >= self.req.max_questions:
            self.ctx.is_completed = True
            log.info("Max questions reached — generating summary.")
            summary_result = await self._generate_summary()
            return InterviewerReply(is_completed=True, summary=summary_result)

        next_q = await self._next_question(review)
        self.ctx.current_question   = next_q.question
        self.ctx.current_topic      = next_q.topic
        self.ctx.current_difficulty = next_q.difficulty
        return self._build_reply(review, next_q)

    async def end_interview(self) -> SummaryResult:
        """Manually end the session early and return the summary."""
        if self.ctx.is_completed:
            raise SessionAlreadyCompletedError()
        if not self.ctx.history:
            raise InvalidInputError("Answer at least one question before ending.")
        self.ctx.is_completed = True
        log.info("Interview manually ended after %d questions.", self.ctx.question_count)
        return await self._generate_summary()