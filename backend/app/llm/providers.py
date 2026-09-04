from typing import Any
from ..config import config
import httpx
import json
import logging
import os
import re
import time

#: Seconds to wait for a completion. Generous because these are batch calls,
#: not interactive ones: the categoriser sends forty merchants at a time, and a
#: structured-output reply for that regularly runs past thirty seconds - which
#: is how a correctly configured provider still reported "0 from the model" on
#: every run, with only a warning in the log to say why. Override with
#: LLM_TIMEOUT_SECONDS where a slow proxy needs longer still.
REQUEST_TIMEOUT = float(os.environ.get("LLM_TIMEOUT_SECONDS", "120"))

#: How many times to wait out a 429 before giving up on a call. Free
#: OpenRouter models allow 20 requests a minute, so a categorisation run
#: with enough unknown merchants will hit the ceiling mid-run; the limit
#: clears within the minute, and the batch is worth waiting for.
RATE_LIMIT_RETRIES = max(
    0, int(os.environ.get("LLM_RATE_LIMIT_RETRIES", "3")))

#: Ceiling on a single wait, however long the provider asks for. A daily
#: quota resets hours away, and blocking an import job until then is
#: worse than failing the batch and saying so.
MAX_RATE_LIMIT_WAIT = float(os.environ.get("LLM_RATE_LIMIT_MAX_WAIT", "60"))


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

def _clamp_wait(seconds: float) -> float:
    return max(0.0, min(seconds, MAX_RATE_LIMIT_WAIT))


def _retry_after_seconds(response: Any, attempt: int) -> float:
    """How long to wait before retrying a rate-limited call.

    OpenRouter sends `Retry-After` (seconds) or `X-RateLimit-Reset` (a
    millisecond epoch) on a 429. Prefer whichever it gave, because guessing
    shorter just spends another request against the same ceiling; fall back
    to doubling waits when it gave neither. Capped so a misread header cannot
    park an import job for an hour.
    """
    headers = getattr(response, "headers", {}) or {}

    raw = headers.get("Retry-After")
    if raw:
        try:
            # Clamped at both ends: an HTTP-date rather than a count of
            # seconds falls through to the header below, and a stale or
            # negative value must not reach time.sleep(), which raises on one.
            return _clamp_wait(float(raw))
        except (TypeError, ValueError):
            pass

    raw = headers.get("X-RateLimit-Reset")
    if raw:
        try:
            wait = float(raw) / 1000.0 - time.time()
            if wait > 0:
                return _clamp_wait(wait)
        except (TypeError, ValueError):
            pass

    return _clamp_wait(2.0 ** attempt)


def _message_text(data: Any) -> str:
    """The model's answer, skipping its thinking.

    Reasoning models on OpenRouter put the chain of thought in a sibling
    field, not in `content`:

        message -> {"content": "[{\"i\": 0, ...}]",
                    "reasoning": "The user wants me to classify..."}

    So `content` is the answer and is read first. But a reasoning model that
    runs out of `max_tokens` mid-thought returns `content: ""` with the whole
    budget spent in `reasoning`, and an empty string reads downstream as a
    silent failure - categorisation reporting "0 from the model" over a
    provider that was answering. Falling back to the reasoning at least gives
    `_parse_json_loose` something to find the answer in.
    """
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        logging.error("Unexpected OpenRouter response: %s", data)
        return ""
    if not isinstance(message, dict):
        logging.error("Unexpected OpenRouter message: %s", message)
        return ""

    content = message.get("content")
    # Some providers return content as a list of parts rather than a string.
    if isinstance(content, list):
        content = "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict)
        )
    if isinstance(content, str) and content.strip():
        return content

    for key in ("reasoning", "reasoning_content"):
        thinking = message.get(key)
        if isinstance(thinking, str) and thinking.strip():
            logging.warning(
                "OpenRouter returned only reasoning (finish_reason=%s); "
                "falling back to it. Raise LLM max_tokens or lower "
                "OPENROUTER_REASONING_EFFORT if this recurs.",
                data.get("choices", [{}])[0].get("finish_reason"),
            )
            return thinking
    return ""


class OpenRouterProvider(Provider):
    """One key, one OpenAI-shaped endpoint, a catalogue of free models.

    `:free` models cost nothing per token but are rate limited per *request*
    - 20 a minute, and 50 a day until the account has bought $10 of credit,
    then 1000 a day. A categorisation run walks its merchants forty to a
    call, so the per-minute ceiling is the one that bites, and a 429 there
    would otherwise lose a whole batch of merchants to a limit that clears in
    seconds. Hence the wait-and-retry below rather than a bare raise.
    """

    @property
    def available(self) -> bool:
        return bool(config.OPENROUTER_API_KEY)

    def _model(self, tier: str) -> str:
        return (config.OPENROUTER_MODEL_FAST if tier == "fast"
                else config.OPENROUTER_MODEL_STRONG)

    def complete(self, prompt: str, system: str = "", max_tokens: int = 4096,
                 tier: str = "fast", temperature: float = 0.0,
                 json_mode: bool = False) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self._model(tier),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # Asking for JSON in the system prompt is a request; this is a
        # constraint. A model left to answer freely prefaces the array with a
        # sentence of explanation, which parses as nothing - which is how a
        # correctly configured provider reports "0 from the model" on every
        # run with only a warning in the log to say why.
        if json_mode and config.OPENROUTER_JSON_MODE:
            payload["response_format"] = {"type": "json_object"}
        if config.OPENROUTER_REASONING_EFFORT:
            payload["reasoning"] = {
                "effort": config.OPENROUTER_REASONING_EFFORT}

        headers = {"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"}
        # Attribution, so a shared key's traffic is identifiable on
        # openrouter.ai. Neither header carries anything about the user.
        if config.OPENROUTER_APP_URL:
            headers["HTTP-Referer"] = config.OPENROUTER_APP_URL
        if config.OPENROUTER_APP_TITLE:
            headers["X-Title"] = config.OPENROUTER_APP_TITLE

        url = f"{config.OPENROUTER_BASE_URL}/chat/completions"
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            for attempt in range(RATE_LIMIT_RETRIES + 1):
                resp = client.post(url, json=payload, headers=headers)
                if resp.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
                    break
                delay = _retry_after_seconds(resp, attempt)
                logging.warning(
                    "OpenRouter rate limited (429); retrying in %.0fs "
                    "(attempt %d of %d)",
                    delay, attempt + 1, RATE_LIMIT_RETRIES)
                time.sleep(delay)

            resp.raise_for_status()
            data = resp.json()

        # OpenRouter reports upstream failures in the body, with a 200 on the
        # envelope that carried them. Left unread, the error surfaces as an
        # empty answer with no explanation anywhere.
        if isinstance(data, dict) and data.get("error"):
            error = data["error"]
            message = (error.get("message") if isinstance(error, dict)
                       else str(error))
            raise RuntimeError(f"OpenRouter returned an error: {message}")

        return _message_text(data)

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
