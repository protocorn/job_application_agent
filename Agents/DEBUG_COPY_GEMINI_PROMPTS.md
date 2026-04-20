# Debug Copy: Gemini Prompt/Response Only

This debug setup creates a copy of the main agent and removes noisy `print()` output while keeping only Gemini prompt/response visibility.

## Files Added

- `Agents/job_application_agent_debug_prompts.py`
- `Agents/gemini_prompt_response_debug.py`

## What This Copy Changes

1. **Silences normal print clutter globally**
   - Calls to `print()` are suppressed once the debug copy is initialized.
   - This removes noisy output like profile-loaded messages, navigation chatter, button-click traces, etc.

2. **Logs Gemini prompts and responses**
   - Every Gemini request/response is logged to console and to:
   - `Agents/logs/gemini_prompt_response_debug.log`

3. **Captures Gemini usage beyond only `job_application_agent`**
   - Logging is added at the Gemini SDK layer (`google.genai.Client.models.generate_content` and legacy `google.generativeai.GenerativeModel.generate_content`).
   - Because of this, prompts/responses from executors, detectors, brains, and other modules are included automatically, not only `job_application_agent`.

## How to Use

Use `RefactoredJobAgent` from:

- `Agents/job_application_agent_debug_prompts.py`

The debug mode auto-enables during `RefactoredJobAgent` initialization:

- print suppression: ON
- Gemini prompt/response logging: ON

## Log Format

Each entry is written as blocks:

- `GEMINI PROMPT`
- `GEMINI RESPONSE`
- model name
- payload text (large payloads are truncated to keep logs manageable)
