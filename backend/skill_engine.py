import os
import json
import time
from typing import List, Dict, Any, Optional
from hybrid_llm import HybridLLMRouter

SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")

class DexterSkillEngine:
    def __init__(self, llm_router: HybridLLMRouter):
        self.llm = llm_router
        os.makedirs(SKILLS_DIR, exist_ok=True)
        self._initialize_default_skills()

    def _initialize_default_skills(self):
        default_skill = {
            "name": "youtube_playlist_curator",
            "description": "Searches and plays top trending music or video playlists on YouTube.",
            "triggers": ["play song", "music playlist", "find music", "play video"],
            "steps": [
                "Extract query keywords from voice command",
                "Invoke Playwright browser / YouTube API to search query",
                "Autoplay first high-rank match",
                "Announce video title to user via TTS speech"
            ],
            "learnedAt": "2026-08-06 20:00:00"
        }
        skill_path = os.path.join(SKILLS_DIR, f"{default_skill['name']}.json")
        if not os.path.exists(skill_path):
            with open(skill_path, "w", encoding="utf-8") as f:
                json.dump(default_skill, f, indent=2)

    def list_skills(self) -> List[Dict[str, Any]]:
        skills = []
        if not os.path.exists(SKILLS_DIR):
            return skills
            
        for file in os.listdir(SKILLS_DIR):
            if file.endswith(".json"):
                try:
                    with open(os.path.join(SKILLS_DIR, file), "r", encoding="utf-8") as f:
                        skills.append(json.load(f))
                except Exception:
                    pass
        return skills

    def learn_skill_from_task(self, task_name: str, task_description: str, execution_log: List[str]) -> Dict[str, Any]:
        prompt = f"""
        Act as Dexter's Self-Learning Agent Memory System.
        Distill the following completed task execution into a structured, reusable skill definition:
        
        Task Name: {task_name}
        Task Goal: {task_description}
        Execution Steps Log: {json.dumps(execution_log)}
        
        Format output strictly as JSON with keys:
        {{
          "name": "snake_case_skill_identifier",
          "description": "Clear explanation of what this learned skill does",
          "triggers": ["phrase1", "phrase2"],
          "steps": ["Step 1 description", "Step 2 description"]
        }}
        Respond ONLY with valid raw JSON. No preambles.
        """
        
        raw_res = self.llm.generate(prompt, provider="auto")
        
        parsed = {}
        try:
            clean_res = raw_res.strip()
            if clean_res.startswith("```json"):
                clean_res = clean_res.split("```json")[1].split("```")[0].strip()
            elif clean_res.startswith("```"):
                clean_res = clean_res.split("```")[1].strip()
            parsed = json.loads(clean_res)
        except Exception:
            clean_id = task_name.lower().replace(" ", "_")
            parsed = {
                "name": clean_id,
                "description": f"Learned skill for: {task_name}",
                "triggers": [task_name.lower()],
                "steps": execution_log if execution_log else ["Execute task action"]
            }

        parsed["learnedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
        filename = f"{parsed.get('name', 'custom_skill')}.json"
        filepath = os.path.join(SKILLS_DIR, filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(parsed, f, indent=2, ensure_ascii=False)
            
        return parsed
