# End-to-End Review: Prioritized Issue Tracker

Date: 2026-04-10  
Status: Initial full-pass backlog from architecture + code audit.

## Scoring
- Severity: P0 (critical), P1 (high), P2 (medium/maintainability)
- Confidence: High / Medium / Low (based on direct code evidence and runtime checks)

## Issues

| ID | Severity | Component | Evidence | Impact | Repro / Validation | Proposed Fix | Owner | ETA |
|---|---|---|---|---|---|---|---|---|
| E2E-001 | P0 | `Agents/job_application_agent.py` | `run_links_with_refactored_agent()` uses `agent` in `finally` paths without guaranteed assignment on constructor failure | Can mask root cause with secondary exception, break cleanup flow | Simulate `RefactoredJobAgent(...)` constructor failure and observe `finally` path behavior | Initialize `agent = None`, guard all `finally` accesses, add regression test | Agent runtime | 0.5 day |
| E2E-002 | P0 | `server/api_server.py`, `server/auth.py`, `server/rate_limiter.py` | Admin checks split across `@require_admin`, manual `os.getenv('ADMIN_EMAILS')` checks, and `rate_limiter.ADMIN_EMAILS` | Authorization drift and privilege inconsistencies | Compare route decorators + inline checks in admin/beta/credits routes | Standardize to single `@require_admin` path and one admin source | Backend API | 1 day |
| E2E-003 | P0 | `server/api_server.py` (`/api/cli/agent-key`) | Endpoint returns runtime key and shared Gemini key to authenticated clients | High-value secret exposure if JWT/session compromised | Call path inspection from `launchway/agent_bootstrap.py` and server response schema | Scope key issuance, short TTL, extra device binding/auditing, reduce payload | Backend + CLI | 1-2 days |
| E2E-004 | P1 | `Agents/job_application_agent.py` | Global Playwright singleton (`_global_playwright_instance`, loop ID state) | Concurrency/lifecycle fragility in long-running and multi-loop scenarios | Static review + lifecycle path from apply/continuous flows | Encapsulate in explicit session manager and inject dependency | Agent runtime | 2 days |
| E2E-005 | P1 | Core executors and agent orchestrator | Broad `except Exception` and bare `except:` in hot paths | Real defects hidden as soft failures; hard debugging | Static scan in reviewed modules + flow audit | Replace with typed exceptions and structured error taxonomy | Agent runtime | 2-3 days |
| E2E-006 | P1 | `server/api_server.py` | Duplicate `/health` and `/api/health` route definitions | Operational ambiguity, inconsistent observability payloads | Route table review in file | Keep one canonical health handler + optional versioned status endpoint | Backend API | 0.5 day |
| E2E-007 | P1 | `server/api_server.py` public reactions/visits endpoints | Unauthenticated write endpoints; delete path based on IP hash helper | Abuse/spam risk and noisy telemetry | Static endpoint/auth review | Add rate limiting + bot controls; require auth for destructive action | Backend API | 1 day |
| E2E-008 | P1 | `launchway/agent_bootstrap.py` diagnostics | Diagnostic structure includes key-bearing fields | Accidental secret leakage risk if logged/exported | Static review of `_bootstrap_diag` fields | Redact/remove key values from diagnostics, store booleans only | CLI/bootstrap | 0.5 day |
| E2E-009 | P2 | `server/google_oauth_service.py` | Fallback random `ENCRYPTION_KEY` behavior if env unset | Token persistence/decryption instability across restarts | Static config review | Fail fast when missing required encryption key in production | Backend API | 0.5 day |
| E2E-010 | P2 | Large agent modules | Multi-thousand-line orchestrator/executor files | Slower review velocity, higher merge conflict/defect probability | File-size + coupling review | Incremental modularization with explicit interfaces | Agent runtime | 1-2 weeks (phased) |
| E2E-011 | P2 | `Agents/resume_tailoring_agent.py` and logging usage | Mixed print/logging patterns and mixed logging stacks across modules | Inconsistent observability and difficult production triage | Static review | Standardize on one logging approach + correlation fields | Agent + backend | 1-2 days |
| E2E-012 | P2 | Source packaging path | Plaintext `Agents/` and encrypted mirrored path increase drift risk | Runtime surprises and maintenance overhead | Packaging/bootstrap review | Add CI drift check and one canonical source pipeline | Build/release | 2 days |

## Verification Notes

- Automated checks executed:
  - `python -m unittest discover -s Testing -v` -> 5 passed.
  - `python -m py_compile` on critical audited modules -> passed.
- Not executed in this pass:
  - Full live E2E external flows (OAuth callback, ATS apply in browser, production credit lifecycle), pending env/service credentials.

## First 5 Fixes (Action Queue)

1. E2E-001 (cleanup/constructor failure guard)
2. E2E-002 (admin check consolidation)
3. E2E-003 (`/api/cli/agent-key` hardening)
4. E2E-005 (exception hygiene in hot paths)
5. E2E-007 (public endpoint abuse controls)
