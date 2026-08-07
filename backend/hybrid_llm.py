import os
import json
import requests
from typing import Dict, Any, List, Optional

class HybridLLMRouter:
    def __init__(self):
        self.lm_studio_url = os.getenv("LM_STUDIO_URL", "http://localhost:1234/v1")
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434/v1")
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "")

    def get_available_providers(self) -> Dict[str, Any]:
        lm_online = self._check_endpoint(f"{self.lm_studio_url}/models")
        ollama_online = self._check_endpoint(f"{self.ollama_url}/models")
        return {
            "lmStudio": {"online": lm_online, "url": self.lm_studio_url},
            "ollama": {"online": ollama_online, "url": self.ollama_url},
            "gemini": {"configured": bool(self.gemini_api_key)},
            "openai": {"configured": bool(self.openai_api_key)}
        }

    def _check_endpoint(self, url: str) -> bool:
        try:
            res = requests.get(url, timeout=1.5)
            return res.status_code == 200
        except Exception:
            return False

    def generate(
        self,
        prompt: str,
        system_prompt: str = "You are Dexter, an intelligent AI super assistant. Be extremely concise, direct, and precise. Keep answers brief (1-3 sentences max) unless detailed information is explicitly requested. Do not use filler or conversational fluff.",
        provider: str = "auto",
        model: Optional[str] = None,
        temperature: float = 0.5,
        max_tokens: int = 256
    ) -> str:
        # Determine provider automatically if 'auto'
        if provider == "auto":
            providers = self.get_available_providers()
            if providers["lmStudio"]["online"]:
                provider = "lmstudio"
            elif providers["gemini"]["configured"]:
                provider = "gemini"
            elif providers["openai"]["configured"]:
                provider = "openai"
            elif providers["ollama"]["online"]:
                provider = "ollama"
            else:
                provider = "lmstudio"  # Fallback try local

        if provider == "gemini" and self.gemini_api_key:
            return self._call_gemini(prompt, system_prompt, model or "gemini-1.5-pro", temperature)
        elif provider == "openai" and self.openai_api_key:
            return self._call_openai(prompt, system_prompt, model or "gpt-4o", temperature, max_tokens)
        elif provider == "ollama":
            return self._call_openai_compat(self.ollama_url, prompt, system_prompt, model or "llama3.2", temperature, max_tokens)
        else:
            return self._call_openai_compat(self.lm_studio_url, prompt, system_prompt, model or "llama-3.2-3b", temperature, max_tokens)

    def _call_openai_compat(self, base_url: str, prompt: str, system_prompt: str, model: str, temperature: float, max_tokens: int) -> str:
        try:
            url = f"{base_url}/chat/completions"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            res = requests.post(url, json=payload, timeout=45)
            if res.status_code == 200:
                data = res.json()
                return data["choices"][0]["message"]["content"]
            else:
                return "I'm having a bit of trouble connecting to my local language model right now. Could you please check if LM Studio or Ollama is running?"
        except Exception as e:
            return "I apologize, but my local reasoning engine seems to be offline at the moment. Please make sure LM Studio is running in the background."

    def _call_gemini(self, prompt: str, system_prompt: str, model: str, temperature: float) -> str:
        try:
            import google.generativeai as genai
            genai.configure(api_key=self.gemini_api_key)
            g_model = genai.GenerativeModel(model_name=model, system_instruction=system_prompt)
            response = g_model.generate_content(prompt)
            return response.text
        except Exception as e:
            return "I'm currently unable to reach the Gemini servers. Please check my API key configuration."

    def _call_openai(self, prompt: str, system_prompt: str, model: str, temperature: float, max_tokens: int) -> str:
        try:
            url = "https://api.openai.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {self.openai_api_key}"}
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            res = requests.post(url, json=payload, headers=headers, timeout=45)
            if res.status_code == 200:
                data = res.json()
                return data["choices"][0]["message"]["content"]
            return f"[OpenAI Response]: {prompt}"
        except Exception as e:
            return f"[OpenAI Error]: {str(e)}"
