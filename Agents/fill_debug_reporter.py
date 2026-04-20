"""
Fill Debug Reporter
===================
When LAUNCHWAY_FILL_DEBUG=true, after every fill_form() call this module
prints a complete, human-readable report to the console AND writes it to
~/.launchway/logs/fill_debug_<timestamp>.log

The report shows:
  - Every detected form field (index, label, category, stable_id)
  - How it was filled (deterministic / learned / ai / skipped / human / failed)
  - The exact value used
  - For AI-filled fields: the complete Gemini prompt + response
  - A summary table at the top

Usage: import fill_debug_reporter; it self-activates when LAUNCHWAY_FILL_DEBUG=true.
"""
import os
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any

_ENABLED = os.getenv("LAUNCHWAY_FILL_DEBUG", "").strip().lower() in {"1", "true", "yes", "y"}

# ── Colour helpers ──────────────────────────────────────────────────────────
_C = {
    "reset":  "\033[0m",
    "bold":   "\033[1m",
    "green":  "\033[92m",
    "yellow": "\033[93m",
    "red":    "\033[91m",
    "cyan":   "\033[96m",
    "blue":   "\033[94m",
    "grey":   "\033[90m",
}

def _c(colour: str, text: str) -> str:
    return f"{_C.get(colour, '')}{text}{_C['reset']}"


# ── Data model ───────────────────────────────────────────────────────────────

class FillEvent:
    __slots__ = ("index", "label", "category", "stable_id", "method",
                 "value", "reason", "gemini_prompt", "gemini_response")

    def __init__(self, index: int, label: str, category: str,
                 stable_id: str = ""):
        self.index = index
        self.label = label
        self.category = category
        self.stable_id = stable_id
        self.method: str = "pending"       # deterministic/learned/ai/skipped/human/failed
        self.value: Any = None
        self.reason: str = ""
        self.gemini_prompt: str = ""
        self.gemini_response: str = ""


# ── Singleton reporter (one per fill_form() call) ─────────────────────────

class FillDebugReporter:
    def __init__(self):
        self._events: dict[str, FillEvent] = {}   # stable_id → event
        self._index = 0
        self._gemini_batch_prompt: str = ""
        self._gemini_batch_response: str = ""
        self._gemini_single_calls: list[dict] = []   # {label, prompt, response}
        self._start_time = datetime.now()

    # ── Field registration ─────────────────────────────────────────────────

    def register_field(self, stable_id: str, label: str,
                       category: str) -> FillEvent:
        if stable_id not in self._events:
            self._index += 1
            ev = FillEvent(self._index, label, category, stable_id)
            self._events[stable_id] = ev
        return self._events[stable_id]

    def _get_or_create(self, stable_id: str, label: str,
                       category: str) -> FillEvent:
        return self.register_field(stable_id, label, category)

    # ── Fill outcome recording ────────────────────────────────────────────

    def record_fill(self, stable_id: str, label: str, category: str,
                    method: str, value: Any, reason: str = ""):
        ev = self._get_or_create(stable_id, label, category)
        ev.method = method
        ev.value = value
        ev.reason = reason

    def record_skip(self, stable_id: str, label: str, category: str,
                    reason: str):
        self.record_fill(stable_id, label, category, "skipped", None, reason)

    def record_human(self, stable_id: str, label: str, category: str):
        self.record_fill(stable_id, label, category, "human", None,
                         "Requires human input")

    def record_failed(self, stable_id: str, label: str, category: str,
                      reason: str = ""):
        self.record_fill(stable_id, label, category, "failed", None, reason)

    # ── Gemini call recording ──────────────────────────────────────────────

    def record_gemini_batch(self, prompt: str, response: str):
        """Called by GeminiFieldMapper for the main batch mapping call."""
        self._gemini_batch_prompt = prompt
        self._gemini_batch_response = response

    def record_gemini_single(self, label: str, category: str,
                              prompt: str, response: str):
        """Called for single-field Gemini calls (dropdown option pick, text gen, etc.)."""
        self._gemini_single_calls.append({
            "label": label, "category": category,
            "prompt": prompt, "response": response,
        })

    # ── Report generation ──────────────────────────────────────────────────

    def generate_report(self, url: str = "") -> str:
        lines: list[str] = []
        sep = "=" * 80

        lines.append(sep)
        lines.append("  LAUNCHWAY FILL DEBUG REPORT")
        if url:
            lines.append(f"  URL : {url}")
        lines.append(f"  Time: {self._start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(sep)

        # ── Field table ──────────────────────────────────────────────────
        lines.append("")
        lines.append("FIELDS DETECTED & FILLED")
        lines.append("-" * 80)
        lines.append(
            f"{'#':>3}  {'Label':<35} {'Category':<22} {'Method':<14} Value / Reason"
        )
        lines.append("-" * 80)

        _METHOD_ICON = {
            "deterministic": "✓ DETERMINISTIC",
            "learned":       "↺ LEARNED",
            "ai":            "★ AI (GEMINI)",
            "skipped":       "⊘ SKIPPED",
            "human":         "👤 HUMAN",
            "failed":        "✗ FAILED",
            "pending":       "? PENDING",
        }

        for ev in sorted(self._events.values(), key=lambda e: e.index):
            icon = _METHOD_ICON.get(ev.method, ev.method.upper())
            raw_val = str(ev.value) if ev.value is not None else f"({ev.reason})"
            # Truncate very long values for table display
            display_val = raw_val[:60] + "…" if len(raw_val) > 61 else raw_val
            lines.append(
                f"{ev.index:>3}  {ev.label:<35} {ev.category:<22} {icon:<14} {display_val}"
            )
        lines.append("-" * 80)

        # Counts
        from collections import Counter
        counts = Counter(ev.method for ev in self._events.values())
        lines.append(
            "  Summary: "
            + "  |  ".join(
                f"{m.upper()}: {n}"
                for m, n in sorted(counts.items())
            )
        )
        lines.append("")

        # ── Gemini batch prompt/response ─────────────────────────────────
        if self._gemini_batch_prompt:
            lines.append(sep)
            lines.append("  GEMINI BATCH MAPPING CALL")
            lines.append(sep)
            lines.append("")
            lines.append("── PROMPT ──────────────────────────────────────────────────────────────────")
            lines.append(self._gemini_batch_prompt)
            lines.append("")
            lines.append("── RESPONSE ────────────────────────────────────────────────────────────────")
            lines.append(self._gemini_batch_response)
            lines.append("")

        # ── Single Gemini calls ──────────────────────────────────────────
        if self._gemini_single_calls:
            lines.append(sep)
            lines.append("  GEMINI SINGLE-FIELD CALLS")
            lines.append(sep)
            for i, call in enumerate(self._gemini_single_calls, 1):
                lines.append(f"\n[Call {i}] Field: {call['label']}  ({call['category']})")
                lines.append("── PROMPT ──────────────────────────────────────────────────────────────────")
                lines.append(call["prompt"])
                lines.append("── RESPONSE ────────────────────────────────────────────────────────────────")
                lines.append(call["response"])
            lines.append("")

        lines.append(sep)
        return "\n".join(lines)

    def print_and_save(self, url: str = ""):
        """Generate report, print to console, and save to file."""
        report = self.generate_report(url)

        # Always save to file
        log_dir = Path.home() / ".launchway" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = self._start_time.strftime("%Y%m%d_%H%M%S")
        log_path = log_dir / f"fill_debug_{ts}.log"
        log_path.write_text(report, encoding="utf-8")

        # Print to console (coloured) only when debug flag is set
        if _ENABLED:
            _print_coloured(report)

        return str(log_path)


# ── Colour-aware console printing ──────────────────────────────────────────

def _print_coloured(report: str):
    """Print the report to console with basic ANSI colouring."""
    print()
    for line in report.splitlines():
        if line.startswith("==="):
            print(_c("bold", _c("cyan", line)))
        elif line.startswith("──"):
            print(_c("grey", line))
        elif "✓ DETERMINISTIC" in line:
            print(_c("green", line))
        elif "↺ LEARNED" in line:
            print(_c("blue", line))
        elif "★ AI (GEMINI)" in line:
            print(_c("yellow", line))
        elif "⊘ SKIPPED" in line:
            print(_c("grey", line))
        elif "👤 HUMAN" in line:
            print(_c("yellow", line))
        elif "✗ FAILED" in line:
            print(_c("red", line))
        else:
            print(line)
    print()


# ── Global active reporter (reset at start of each fill_form call) ──────────

_active: FillDebugReporter | None = None


def start_report() -> FillDebugReporter:
    global _active
    _active = FillDebugReporter()
    return _active


def get_reporter() -> FillDebugReporter | None:
    return _active


def finish_report(url: str = "") -> str:
    """Finalise and save the report. Returns path to saved file."""
    global _active
    if _active is None:
        return ""
    path = _active.print_and_save(url)
    return path
