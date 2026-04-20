from __future__ import annotations

import builtins
import json
import os
import threading
from datetime import datetime
from typing import Any

_ORIGINAL_PRINT = builtins.print
_IS_ENABLED = False
_PATCH_LOCK = threading.Lock()
_WRITE_LOCK = threading.Lock()
_LOG_FILE_PATH = ""


def _safe_text(value: Any, max_len: int = 8000) -> str:
    """Convert any Gemini payload object to bounded text."""
    if value is None:
        return ""

    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, default=str, ensure_ascii=True, indent=2)
        except Exception:
            text = str(value)

    if len(text) > max_len:
        return f"{text[:max_len]}... [truncated {len(text) - max_len} chars]"
    return text


def _extract_response_text(response: Any) -> str:
    """Extract text from new and legacy Gemini response objects."""
    try:
        text = getattr(response, "text", None)
        if text:
            return str(text)
    except Exception:
        pass

    try:
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            content = getattr(candidates[0], "content", None)
            parts = getattr(content, "parts", None) if content else None
            if parts:
                maybe_text = getattr(parts[0], "text", None)
                if maybe_text:
                    return str(maybe_text)
    except Exception:
        pass

    return _safe_text(response)


def _emit_debug_log(section_title: str, text: str) -> None:
    """Write one debug block to console and file."""
    global _LOG_FILE_PATH
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    block = (
        f"\n{'=' * 96}\n"
        f"[{timestamp}] {section_title}\n"
        f"{'-' * 96}\n"
        f"{text}\n"
        f"{'=' * 96}\n"
    )

    # Use original print so this still appears while normal print() is silenced.
    _ORIGINAL_PRINT(block)

    if _LOG_FILE_PATH:
        with _WRITE_LOCK:
            with open(_LOG_FILE_PATH, "a", encoding="utf-8") as handle:
                handle.write(block)


def _patch_print_suppression() -> None:
    def _silent_print(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        return None

    builtins.print = _silent_print


def _patch_google_genai_client() -> None:
    from google import genai as google_genai

    original_client_cls = google_genai.Client

    # Idempotency guard
    if getattr(original_client_cls, "_prompt_debug_wrapped", False):
        return

    class DebugClient(original_client_cls):  # type: ignore[misc]
        _prompt_debug_wrapped = True

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._wrap_models_generate_content()

        def _wrap_models_generate_content(self) -> None:
            models_obj = getattr(self, "models", None)
            if not models_obj:
                return

            original_generate = getattr(models_obj, "generate_content", None)
            if not callable(original_generate):
                return
            if getattr(original_generate, "_prompt_debug_wrapped", False):
                return

            def wrapped_generate_content(*args: Any, **kwargs: Any) -> Any:
                model_name = kwargs.get("model", "")
                contents = kwargs.get("contents")
                if contents is None and len(args) >= 2:
                    contents = args[1]
                if not model_name and len(args) >= 1:
                    model_name = str(args[0])

                _emit_debug_log(
                    "GEMINI PROMPT",
                    f"model: {model_name}\n\n{_safe_text(contents)}",
                )

                response = original_generate(*args, **kwargs)

                _emit_debug_log(
                    "GEMINI RESPONSE",
                    f"model: {model_name}\n\n{_extract_response_text(response)}",
                )
                return response

            wrapped_generate_content._prompt_debug_wrapped = True  # type: ignore[attr-defined]
            models_obj.generate_content = wrapped_generate_content

    google_genai.Client = DebugClient


def _patch_legacy_google_generativeai() -> None:
    try:
        import google.generativeai as legacy_genai
    except Exception:
        return

    model_cls = getattr(legacy_genai, "GenerativeModel", None)
    if model_cls is None:
        return

    original_generate = getattr(model_cls, "generate_content", None)
    if not callable(original_generate):
        return
    if getattr(original_generate, "_prompt_debug_wrapped", False):
        return

    def wrapped_generate_content(self: Any, contents: Any, **kwargs: Any) -> Any:
        model_name = getattr(self, "model_name", "") or getattr(self, "_model_name", "")

        _emit_debug_log(
            "GEMINI PROMPT",
            f"model: {model_name}\n\n{_safe_text(contents)}",
        )

        response = original_generate(self, contents, **kwargs)

        _emit_debug_log(
            "GEMINI RESPONSE",
            f"model: {model_name}\n\n{_extract_response_text(response)}",
        )
        return response

    wrapped_generate_content._prompt_debug_wrapped = True  # type: ignore[attr-defined]
    model_cls.generate_content = wrapped_generate_content


def enable_gemini_prompt_response_debug(
    log_path: str | None = None,
    suppress_prints: bool = True,
) -> str:
    """
    Enable debug mode:
      1) suppresses all normal print() output,
      2) prints Gemini prompt/response pairs,
      3) appends Gemini prompt/response pairs to a log file.
    """
    global _IS_ENABLED, _LOG_FILE_PATH

    with _PATCH_LOCK:
        if _IS_ENABLED:
            return _LOG_FILE_PATH

        if log_path is None:
            log_path = os.path.join(os.path.dirname(__file__), "logs", "gemini_prompt_response_debug.log")

        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        _LOG_FILE_PATH = log_path

        if suppress_prints:
            _patch_print_suppression()

        _patch_google_genai_client()
        _patch_legacy_google_generativeai()

        _IS_ENABLED = True
        _emit_debug_log(
            "GEMINI DEBUG MODE ENABLED",
            "All print() output is suppressed. Only Gemini prompt/response logging remains active.",
        )

    return _LOG_FILE_PATH
