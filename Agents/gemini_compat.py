"""
Compatibility shim: exposes a 'genai' object that supports both the old
google.generativeai API style (genai.configure + genai.GenerativeModel)
AND the new google.genai style (genai.Client), using google.genai internally.

Usage in agent files:
    from gemini_compat import genai
    genai.configure(api_key=my_key)          # set global key
    model = genai.GenerativeModel("gemini-2.5-flash")
    response = model.generate_content(prompt)
    print(response.text)

    # New-SDK style still works too:
    client = genai.Client(api_key=my_key)
"""

import logging
import os
import random
import time
from typing import Any

from google import genai as _genai_new

logger = logging.getLogger(__name__)


def _call_with_backoff(fn, *args, max_retries: int = 6, **kwargs) -> Any:
    """
    Call *fn(*args, **kwargs)* with exponential backoff on 429 / RESOURCE_EXHAUSTED.

    Wait schedule (seconds): 2, 4, 8, 16, 32, 64  (+jitter ±0.5s each)
    After max_retries attempts the last exception is re-raised.
    """
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            err = str(exc)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                if attempt == max_retries - 1:
                    raise
                wait = (2 ** (attempt + 1)) + random.uniform(-0.5, 0.5)
                logger.warning(
                    f"Gemini 429 rate-limit hit — backing off {wait:.1f}s "
                    f"(attempt {attempt + 1}/{max_retries})"
                )
                print(
                    f"[WARN] Gemini rate limit (429) — retrying in {wait:.1f}s "
                    f"(attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(wait)
            else:
                raise

# Module-level key set via genai.configure()
_API_KEY: str = ""


def _get_api_key() -> str:
    """Return the best available API key: configured > env vars."""
    return (
        _API_KEY
        or os.getenv("GOOGLE_API_KEY", "")
        or os.getenv("GEMINI_API_KEY", "")
    )


# ── Response compatibility classes ──────────────────────────────────────────

class _CompatPart:
    def __init__(self, text: str):
        self.text = text


class _CompatContent:
    def __init__(self, text: str):
        self.parts = [_CompatPart(text)]


class _CompatCandidate:
    """Mimics google.generativeai Candidate."""
    def __init__(self, text: str):
        self.content = _CompatContent(text)


class _CompatResponse:
    """Mimics google.generativeai GenerateContentResponse."""
    def __init__(self, text: str):
        self._text = text
        self.candidates = [_CompatCandidate(text)]

    @property
    def text(self) -> str:
        return self._text


def _extract_text(response: Any) -> str:
    """Robustly extract a text string from a google.genai response."""
    try:
        if hasattr(response, "text") and response.text:
            return response.text
    except Exception:
        pass
    try:
        if hasattr(response, "candidates") and response.candidates:
            cand = response.candidates[0]
            if hasattr(cand, "content") and cand.content:
                if hasattr(cand.content, "parts") and cand.content.parts:
                    return cand.content.parts[0].text
    except Exception:
        pass
    return ""


# ── GenerationConfig ─────────────────────────────────────────────────────────

class GenerationConfig:
    """
    Drop-in replacement for google.generativeai.GenerationConfig.
    Stores generation parameters and converts to google.genai types on demand.
    """

    def __init__(
        self,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        **kwargs,
    ):
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.top_p = top_p
        self.top_k = top_k
        self._extra = kwargs

    def to_genai_config(self):
        """Convert to google.genai GenerateContentConfig."""
        try:
            from google.genai import types as _types
            params = {}
            if self.temperature is not None:
                params["temperature"] = self.temperature
            if self.max_output_tokens is not None:
                params["max_output_tokens"] = self.max_output_tokens
            if self.top_p is not None:
                params["top_p"] = self.top_p
            if self.top_k is not None:
                params["top_k"] = self.top_k
            return _types.GenerateContentConfig(**params)
        except Exception:
            return None


# ── GenerativeModel ──────────────────────────────────────────────────────────

class GenerativeModel:
    """
    Drop-in replacement for google.generativeai.GenerativeModel.

    Internally calls the new google.genai SDK so no deprecated package
    is needed.
    """

    def __init__(self, model_name: str = "gemini-2.5-flash", **kwargs):
        self.model_name = model_name

    def generate_content(self, contents: Any, **kwargs) -> _CompatResponse:
        api_key = _get_api_key()
        if not api_key:
            raise ValueError(
                "Missing key inputs argument! To use the Google AI API, provide (`api_key`) "
                "arguments. To use the Google Cloud API, provide (`vertexai`, `project` & "
                "`location`) arguments."
            )

        # Normalise contents to a plain string prompt
        if isinstance(contents, str):
            prompt = contents
        elif isinstance(contents, list):
            parts: list[str] = []
            for item in contents:
                if isinstance(item, str):
                    parts.append(item)
                elif hasattr(item, "text"):
                    parts.append(str(item.text))
                elif isinstance(item, dict):
                    parts.append(str(item.get("text", item)))
                else:
                    parts.append(str(item))
            prompt = "\n".join(parts)
        else:
            prompt = str(contents)

        client = _genai_new.Client(api_key=api_key)
        gen_config = None
        raw_config = kwargs.get("generation_config")
        if raw_config is not None:
            if isinstance(raw_config, GenerationConfig):
                gen_config = raw_config.to_genai_config()
            # If someone passed a native google.genai config, pass it through
            elif hasattr(raw_config, "__class__") and "GenerateContentConfig" in type(raw_config).__name__:
                gen_config = raw_config
        call_kwargs = {"model": self.model_name, "contents": prompt}
        if gen_config is not None:
            call_kwargs["config"] = gen_config
        raw = _call_with_backoff(client.models.generate_content, **call_kwargs)
        return _CompatResponse(_extract_text(raw))


# ── Namespace object ─────────────────────────────────────────────────────────

class _CompatNamespace:
    """
    Singleton namespace that mimics the google.generativeai module.

    Supported API surface:
      genai.configure(api_key=...)     - set the global API key
      genai.GenerativeModel(name)      - create a model (old-style)
      genai.Client(api_key=...)        - new-SDK client (passed through)
    """

    # Expose classes as attributes so isinstance checks work
    GenerativeModel = GenerativeModel
    GenerationConfig = GenerationConfig

    @staticmethod
    def configure(api_key: str = "", **kwargs) -> None:
        global _API_KEY
        _API_KEY = api_key or ""

    # New-SDK Client passed through directly
    Client = _genai_new.Client

    # Expose types namespace for any code that accesses genai.types
    @property
    def types(self):
        return getattr(_genai_new, "types", None)


# The singleton that agent files import
genai = _CompatNamespace()
