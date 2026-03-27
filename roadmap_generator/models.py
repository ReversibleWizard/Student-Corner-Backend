from pydantic import BaseModel, Field
from typing import List, Literal, Any, Dict, Optional

class RoadmapInitRequest(BaseModel):
    goal_type: Literal["skill", "role"]
    goal: str
    current_level: Literal["beginner", "intermediate", "advanced"]
    known_skills: List[str] = Field(default_factory=list)
    timeline: str

class ChatRequest(BaseModel):
    message: str

class RoadmapResponse(BaseModel):
    session_id: str
    version: int
    goal: str
    prerequisites: List[str]
    roadmap: List[Any]
    graph: Dict[str, List[Any]]

class TerminateResponse(BaseModel):
    message: str
    status: str