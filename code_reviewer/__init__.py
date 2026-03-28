"""
AI Code Review Agent — Multi-agent pipeline for code analysis and optimization.
 
Flow:
  Claude Understander → Claude Technical Reviewer → GPT Quality Reviewer
  → Claude Optimizer (validated by GPT) → Claude Chat Refiner (validated by GPT)
"""