import json
from .data import ROADMAP_DB

def build_roadmap_and_graph(normalized_goal: str, known_skills: list[str]) -> str:
    """Standard python function to fetch roadmap structure and build graph arrays."""
    
    # Try to find a match (case-insensitive)
    goal_key = next((k for k in ROADMAP_DB.keys() if k.lower() == normalized_goal.lower()), None)
    
    if not goal_key:
        return json.dumps({"error": f"Goal '{normalized_goal}' is not supported in our current database."})

    roadmap_data = ROADMAP_DB[goal_key]
    root_id = goal_key.lower().replace(" ", "_")
    nodes = [{"id": root_id, "label": goal_key}]
    edges = []
    phases = []
    
    known_lower = {s.lower() for s in known_skills}
    missing_skills = [p for p in roadmap_data["prerequisites"] if p.lower() not in known_lower]

    for idx, (title, content) in enumerate(roadmap_data["sections"].items()):
        topics = list(content.keys()) if isinstance(content, dict) else content
        phases.append({"phase": f"Phase {idx+1}", "title": title, "topics": topics})
        
        section_id = title.lower().replace(" ", "_")
        nodes.append({"id": section_id, "label": title})
        edges.append({"source": root_id, "target": section_id})
    
    return json.dumps({
        "goal": goal_key,
        "prerequisites": roadmap_data["prerequisites"],
        "missing_skills": missing_skills,
        "roadmap": phases,
        "graph": {"nodes": nodes, "edges": edges}
    })