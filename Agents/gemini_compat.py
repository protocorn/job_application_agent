"""
Compatibility shim: exposes a 'genai' object that supports both the old
google.generativeai API style (genai.configure + genai.GenerativeModel)
AND the new google.genai style (genai.Client), using google.genai internally.

Usage in agent files:
    from gemini_compat import genai
    genai.configure(api_key=my_key)          # set global key
    model = genai.GenerativeModel("gemini-2.0-flash")
    response = model.generate_content(prompt)
    print(response.text)

    # New-SDK style still works too:
    client = genai.Client(api_key=my_key)
"""

import logging
import os
from typing import Any

from google import genai as _genai_new

logger = logging.getLogger(__name__)

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


# ── GenerativeModel ──────────────────────────────────────────────────────────

class GenerativeModel:
    """
    Drop-in replacement for google.generativeai.GenerativeModel.

    Internally calls the new google.genai SDK so no deprecated package
    is needed.
    """

    def __init__(self, model_name: str = "gemini-2.0-flash", **kwargs):
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
        raw = client.models.generate_content(model=self.model_name, contents=prompt)
        return _CompatResponse(_extract_text(raw))


# ── Namespace object ─────────────────────────────────────────────────────────

class _CompatNamespace:
    """
    Singleton namespace that mimics the google.generativeai module.

    Supported API surface:
      genai.configure(api_key=...)     — set the global API key
      genai.GenerativeModel(name)      — create a model (old-style)
      genai.Client(api_key=...)        — new-SDK client (passed through)
    """

    # Expose classes as attributes so isinstance checks work
    GenerativeModel = GenerativeModel

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
