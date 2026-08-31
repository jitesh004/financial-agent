from typing import Any
from ..config import config
import httpx
import json
import logging
import os
import re

#: Seconds to wait for a completion. Generous because these are batch calls,
#: not interactive ones: the categoriser sends forty merchants at a time, and a
#: structured-output reply for that regularly runs past thirty seconds - which
#: is how a correctly configured provider still reported "0 from the model" on
#: every run, with only a warning in the log to say why. Override with
#: LLM_TIMEOUT_SECONDS where a slow proxy needs longer still.
REQUEST_TIMEOUT = float(os.environ.get("LLM_TIMEOUT_SECONDS", "120"))


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

def _gemini_text(data: Any) -> str:
    """The model's answer, skipping its thinking.

    Gemini 2.5 returns the chain of thought as an ordinary part flagged
    `"thought": true`, ahead of the real reply:

        parts[0] -> {"text": "*   Input: A numbered list of...", "thought": true}
        parts[1] -> {"text": "[{\"i\": 0, \"category\": ...}]"}

    Reading parts[0] therefore returned the reasoning every single time. With
    JSON asked for it parsed as nothing, so categorisation reported "0 from the
    model" on a provider that was answering perfectly well; without it, the
    reasoning would have been used as the answer, which is worse.
    """
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        logging.error("Unexpected Gemini response: %s", data)
        return ""
    answer = "".join(str(p.get("text", "")) for p in parts if not p.get("thought"))
    if answer.strip():
        return answer
    # Nothing but thinking came back - fall back to everything rather than
    # returning an empty string that reads as a silent failure downstream.
    return "".join(str(p.get("text", "")) for p in parts)


class GeminiProvider(Provider):
    @property
    def available(self) -> bool:
        return bool(config.GEMINI_API_KEY)

    def complete(self, prompt: str, system: str = "", max_tokens: int = 4096,
                 tier: str = "fast", temperature: float = 0.0,
                 json_mode: bool = False) -> str:
        model = config.GEMINI_MODEL_FAST if tier == "fast" else config.GEMINI_MODEL_STRONG
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={config.GEMINI_API_KEY}"
        
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                # Asking for JSON in the system prompt is a request; this is a
                # constraint. Gemini 2.5 thinks out loud by default and replied
                # with a restatement of the task - "Input: A numbered list of
                # 22 transaction descriptions..." - which parsed as nothing, so
                # every categorisation run reported zero from the model.
                **({"responseMimeType": "application/json"} if json_mode else {}),
            }
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
            
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return _gemini_text(data)

    def complete_json(self, prompt: str, system: str = "", max_tokens: int = 4096, tier: str = "fast") -> Any:
        system = system or "You return only valid JSON. No prose, no code fences."
        raw = self.complete(prompt, system=system, max_tokens=max_tokens,
                            tier=tier, temperature=0.0, json_mode=True)
        return _parse_json_loose(raw)

class AzureOpenAIProvider(Provider):
    @property
    def available(self) -> bool:
        return bool(config.AZURE_OPENAI_ENDPOINT and config.AZURE_OPENAI_API_KEY)
        
    def complete(self, prompt: str, system: str = "", max_tokens: int = 4096, tier: str = "fast", temperature: float = 0.0) -> str:
        deployment = config.AZURE_OPENAI_DEPLOYMENT_FAST if tier == "fast" else config.AZURE_OPENAI_DEPLOYMENT_STRONG
        base_url = config.AZURE_OPENAI_ENDPOINT.rstrip('/')
        if config.AZURE_OPENAI_USE_CLASSIC:
            # The original per-deployment surface.
            url = (f"{base_url}/openai/deployments/{deployment}/chat/completions"
                   f"?api-version={config.AZURE_OPENAI_API_VERSION}")
        else:
            # The OpenAI-compatible surface, where the deployment is named in
            # the body as `model` rather than in the path.
            url = f"{base_url}/openai/v1/chat/completions"

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        if not config.AZURE_OPENAI_USE_CLASSIC:
            payload["model"] = deployment

        headers = {"api-key": config.AZURE_OPENAI_API_KEY,
                   "Authorization": f"Bearer {config.AZURE_OPENAI_API_KEY}"}
        
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            try:
                return data["choices"][0]["message"].get("content", "")
            except (KeyError, IndexError):
                logging.error(f"Unexpected Azure response: {data}")
                return ""

    def complete_json(self, prompt: str, system: str = "", max_tokens: int = 4096, tier: str = "fast") -> Any:
        system = system or "You return only valid JSON. No prose, no code fences."
        raw = self.complete(prompt, system=system, max_tokens=max_tokens, tier=tier, temperature=0.0)
        return _parse_json_loose(raw)
