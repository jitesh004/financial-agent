from typing import Any
from ..config import config
import httpx
import json
import re


def _parse_json_loose(raw: str) -> Any:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue

    raise ValueError(f"Model did not return parseable JSON: {raw[:200]!r}")

class Provider:
    def complete(self, prompt: str, system: str = "", max_tokens: int = 4096, tier: str = "fast", temperature: float = 0.0) -> str:
        raise NotImplementedError
    def complete_json(self, prompt: str, system: str = "", max_tokens: int = 4096, tier: str = "fast") -> Any:
        raise NotImplementedError
    @property
    def available(self) -> bool:
        return False

class GeminiProvider(Provider):
    @property
    def available(self) -> bool:
        return bool(config.GEMINI_API_KEY)

    def complete(self, prompt: str, system: str = "", max_tokens: int = 4096, tier: str = "fast", temperature: float = 0.0) -> str:
        model = config.GEMINI_MODEL_FAST if tier == "fast" else config.GEMINI_MODEL_STRONG
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={config.GEMINI_API_KEY}"
        
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens
            }
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
            
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]

    def complete_json(self, prompt: str, system: str = "", max_tokens: int = 4096, tier: str = "fast") -> Any:
        system = system or "You return only valid JSON. No prose, no code fences."
        raw = self.complete(prompt, system=system, max_tokens=max_tokens, tier=tier, temperature=0.0)
        return _parse_json_loose(raw)

class AzureOpenAIProvider(Provider):
    @property
    def available(self) -> bool:
        return bool(config.AZURE_OPENAI_ENDPOINT and config.AZURE_OPENAI_API_KEY)
        
    def complete(self, prompt: str, system: str = "", max_tokens: int = 4096, tier: str = "fast", temperature: float = 0.0) -> str:
        deployment = config.AZURE_OPENAI_DEPLOYMENT_FAST if tier == "fast" else config.AZURE_OPENAI_DEPLOYMENT_STRONG
        base_url = config.AZURE_OPENAI_ENDPOINT.rstrip('/')
        url = f"{base_url}/openai/deployments/{deployment}/chat/completions?api-version={config.AZURE_OPENAI_API_VERSION}"
        
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        headers = {"api-key": config.AZURE_OPENAI_API_KEY}
        
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    def complete_json(self, prompt: str, system: str = "", max_tokens: int = 4096, tier: str = "fast") -> Any:
        system = system or "You return only valid JSON. No prose, no code fences."
        raw = self.complete(prompt, system=system, max_tokens=max_tokens, tier=tier, temperature=0.0)
        return _parse_json_loose(raw)
