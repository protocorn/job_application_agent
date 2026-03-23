"""
Human Fill Tracker

Attaches to a live Playwright page at page-load time and silently observes
every field the human fills or changes.  It does NOT need to know in advance
which fields the AI left empty — it snapshots field values the moment it
attaches and uses that as the baseline:

  - field was empty at attach time, human typed something  → 'human_fill'
  - field already had a value, human changed it            → 'human_correction'

Lifecycle
---------
  tracker = HumanFillTracker(page, user_id, recorder, site_url)
  await tracker.attach()          ← call once per page / tab

  The tracker runs forever in the background.  Debounce flushes saves to DB
  every DEBOUNCE_SECONDS after the last change.  No "press Enter" required.

  If you want an immediate flush (e.g. on tab close or agent shutdown):
  await tracker.flush_now()

Multi-tab / SPA navigation
--------------------------
  Call tracker.attach() again after page navigation if the page object changes.
  Each call re-injects the JS listener and refreshes the baseline snapshot.
"""

import asyncio
import re
import json
from datetime import datetime
from typing import Dict, Optional, Any
from loguru import logger


# --------------------------------------------------------------------------- #
#  JavaScript injected into the page                                           #
# --------------------------------------------------------------------------- #

_TRACKER_JS = r"""
(function attachHumanFillTracker(callbackName) {

    // Prevent double-injection across navigations
    if (window.__humanFillTrackerAttached) return;
    window.__humanFillTrackerAttached = true;

    // Snapshot of values at the moment this script ran
    // { normalisedLabel: {value, type} }
    var _initialValues = {};

    // Current buffer of human changes
    // { normalisedLabel: {value, type, wasEmpty, ts} }
    window.__humanFillCaptures = {};

    // ── label extraction ──────────────────────────────────────────────────

    // For radio/checkbox groups: walk up the DOM to find the GROUP question,
    // not the individual option label.
    // Priority: fieldset>legend → role=group/radiogroup aria-label → labelledby
    function extractGroupLabel(el) {
        var parent = el.parentElement;
        for (var i = 0; i < 10 && parent; i++) {
            var tag = parent.tagName ? parent.tagName.toLowerCase() : '';

            // Most semantic: <fieldset><legend>Question text</legend>
            if (tag === 'fieldset') {
                var legend = parent.querySelector('legend');
                if (legend && legend.innerText.trim()) return legend.innerText.trim();
            }

            // ARIA group/radiogroup with aria-label
            var role = (parent.getAttribute('role') || '').toLowerCase();
            if (role === 'group' || role === 'radiogroup') {
                var al = parent.getAttribute('aria-label');
                if (al && al.trim()) return al.trim();
                var lby = parent.getAttribute('aria-labelledby');
                if (lby) {
                    // labelledby can be a space-separated list of IDs
                    var ids = lby.split(/\s+/);
                    var parts = [];
                    for (var j = 0; j < ids.length; j++) {
                        var el2 = document.getElementById(ids[j]);
                        if (el2 && el2.innerText.trim()) parts.push(el2.innerText.trim());
                    }
                    if (parts.length) return parts.join(' ');
                }
            }

            // Heading or question element immediately before the group
            // (common in custom UI frameworks that don't use fieldset)
            var prev = parent.previousElementSibling;
            if (prev) {
                var prevTag = prev.tagName ? prev.tagName.toLowerCase() : '';
                if (/^h[1-6]$/.test(prevTag) && prev.innerText.trim())
                    return prev.innerText.trim();
                // div/span/p acting as a label with a "label-like" class
                if (/label|question|title|heading/i.test(prev.className || '') &&
                        prev.innerText.trim())
                    return prev.innerText.trim();
            }

            parent = parent.parentElement;
        }
        return null;  // group label not found — caller decides what to do
    }

    function extractLabel(el) {
        // Radio and checkbox: use the GROUP label so we capture the question,
        // not the individual option text.
        var t = (el.type || '').toLowerCase();
        if (t === 'radio' || t === 'checkbox') {
            var groupLabel = extractGroupLabel(el);
            if (groupLabel) return groupLabel;
            // If we can't find the group label, return null so the capture
            // is skipped — storing an option label as the field label is worse
            // than storing nothing.
            return null;
        }

        if (el.id) {
            var lbl = document.querySelector('label[for="' + el.id + '"]');
            if (lbl && lbl.innerText.trim()) return lbl.innerText.trim();
        }
        if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();
        var lblId = el.getAttribute('aria-labelledby');
        if (lblId) {
            var lblEl = document.getElementById(lblId);
            if (lblEl && lblEl.innerText.trim()) return lblEl.innerText.trim();
        }
        if (el.placeholder && el.placeholder.trim()) return el.placeholder.trim();
        var parent = el.parentElement;
        for (var i = 0; i < 6 && parent; i++) {
            var lbl2 = parent.querySelector('label, [class*="label"], [class*="Label"]');
            if (lbl2 && lbl2 !== el && lbl2.innerText.trim()) return lbl2.innerText.trim();
            parent = parent.parentElement;
        }
        return el.getAttribute('data-field-label') || el.name || el.id || null;
    }

    function fieldCategory(el) {
        var tag = el.tagName.toLowerCase();
        if (tag === 'select')   return 'dropdown';
        if (tag === 'textarea') return 'textarea';
        var t = (el.type || '').toLowerCase();
        if (t === 'radio')    return 'radio_group';
        if (t === 'checkbox') return 'checkbox';
        if (t === 'email')    return 'email_input';
        if (t === 'tel')      return 'tel_input';
        if (t === 'file')     return 'file_upload';
        return 'text_input';
    }

    // Return the visible label text for the checkbox option itself
    // (not the group question — that comes from extractGroupLabel/extractLabel).
    // Priority: <label for=id> → aria-label → aria-labelledby → parent <label>
    function checkboxOptionLabel(el) {
        if (el.id) {
            var lbl = document.querySelector('label[for="' + el.id + '"]');
            if (lbl && lbl.innerText.trim()) return lbl.innerText.trim();
        }
        var ariaLabel = el.getAttribute('aria-label');
        if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();
        var lblId = el.getAttribute('aria-labelledby');
        if (lblId) {
            var lblEl = document.getElementById(lblId);
            if (lblEl && lblEl.innerText.trim()) return lblEl.innerText.trim();
        }
        var parent = el.parentElement;
        if (parent) {
            if (parent.tagName.toLowerCase() === 'label' && parent.innerText.trim())
                return parent.innerText.trim();
            var sibLbl = parent.querySelector('label');
            if (sibLbl && sibLbl !== el && sibLbl.innerText.trim())
                return sibLbl.innerText.trim();
        }
        return null;
    }

    function currentValue(el) {
        var tag = el.tagName.toLowerCase();
        if (tag === 'select') {
            return el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : '';
        }
        if (el.type === 'checkbox') {
            // Unchecked → nothing to record; let the '' guard in handleChange drop it.
            if (!el.checked) return '';
            // Return the semantic label of the selected option so we store
            // e.g. "Yes" / "I agree" instead of the opaque boolean "true".
            // If we cannot determine the label, return '' → capture is skipped.
            return checkboxOptionLabel(el) || '';
        }
        if (el.type === 'radio') {
            var group = document.querySelectorAll(
                'input[type=radio][name="' + el.name + '"]'
            );
            for (var j = 0; j < group.length; j++) {
                if (group[j].checked) {
                    var rl = document.querySelector('label[for="' + group[j].id + '"]');
                    return rl ? rl.innerText.trim() : group[j].value;
                }
            }
            return '';
        }
        return el.value || '';
    }

    function normalise(label) {
        return label.toLowerCase().replace(/[^a-z0-9\s]/g, '').replace(/\s+/g, ' ').trim();
    }

    // ── snapshot initial state of a field ────────────────────────────────
    function snapshotField(el) {
        var label = extractLabel(el);
        if (!label) return;
        var key = normalise(label);
        if (!(key in _initialValues)) {
            _initialValues[key] = {
                value: currentValue(el),
                type:  fieldCategory(el)
            };
        }
    }

    // ── comprehensive noise filter ───────────────────────────────────────
    //
    // A capture is valuable only if BOTH the label AND value pass quality
    // checks.  Every rule below has a documented root cause so it's easy to
    // extend rather than patch.

    // Words that indicate a label is a UI action / navigation element, not
    // a real form question.
    var ACTION_LABEL_RE = /^(type |enter |click |select |add |search |upload |choose |pick |please |start |begin )/i;
    var NAV_LABEL_RE    = /^(next|back|previous|submit|cancel|clear|save|continue|ok|done|finish|skip|close|apply|update|delete|remove|edit|open|show|hide|toggle|confirm|reset|refresh|load|more|less|all|none)$/i;

    // HTML element name patterns — leaked when there is no visible label and
    // the tracker falls back to el.name or el.id.
    // Signals: camelCase, kebab-case, common prefixes, no whitespace.
    var HTML_NAME_RE = /^(btn|input|field|el|frm|txt|chk|sel|ddl|rb|cb|lbl|div|span|form|ctrl|val|data|item|row|col)/i;

    function looksLikeHtmlName(label) {
        if (/\s/.test(label)) return false;          // real labels have spaces
        if (HTML_NAME_RE.test(label)) return true;   // known element name prefixes
        if (/[-_]/.test(label)) return true;         // kebab-case or snake_case
        // camelCase: no spaces but has an uppercase letter that isn't the first char
        if (/[a-z][A-Z]/.test(label)) return true;
        return false;
    }

    function isNoisyCapture(label, value) {
        var lLow = label.toLowerCase().trim();
        var vLow = value.toLowerCase().trim();

        // ── Label quality ─────────────────────────────────────────────────

        // Autocomplete state pollution: aria-label gets injected with
        // live dropdown content → newlines appear or label grows very long.
        if (label.indexOf('\n') !== -1)  return true;
        if (label.length > 120)          return true;

        // Meaninglessly short
        if (label.length < 2)            return true;

        // Pure number (internal index, field count, etc.)
        if (/^\d+$/.test(label.trim()))  return true;

        // Looks like an HTML element name leaked as label
        if (looksLikeHtmlName(label))    return true;

        // UI action / instruction text used as label
        if (ACTION_LABEL_RE.test(label)) return true;

        // Navigation or button text captured as a "field"
        if (NAV_LABEL_RE.test(lLow))     return true;

        // ── Label–value relationship ──────────────────────────────────────

        // Option captured instead of question:
        //   radio "Yes" option → label="Yes", value="Yes"
        if (lLow === vLow)               return true;

        // Autocomplete "X selected" pattern:
        //   label contains the value AND a state word → intermediate state
        if (lLow.indexOf(vLow) !== -1 &&
            /\b(selected|available|results|result|option|options|match|matches|found)\b/.test(lLow)) return true;

        // ── Value quality ─────────────────────────────────────────────────

        // Browser security placeholder for file inputs (always blocked at
        // field_type level too, but double-guard here)
        if (/fakepath/i.test(value))     return true;

        // Transient / meaningless values
        if (vLow === '' || vLow === '-' || vLow === '--' || vLow === 'n/a') return true;

        return false;
    }

    // ── handle a change event ────────────────────────────────────────────
    function handleChange(el) {
        var label = extractLabel(el);
        if (!label) return;
        var value = currentValue(el);
        if (!value || !value.trim()) return;   // ignore clears / blank selections

        if (isNoisyCapture(label, value)) return;  // drop noise before hitting Python

        var key      = normalise(label);
        var initData = _initialValues[key] || {value: ''};
        var wasEmpty = !initData.value || !initData.value.trim();

        window.__humanFillCaptures[label] = {
            value:    value.trim(),
            type:     fieldCategory(el),
            wasEmpty: wasEmpty,
            ts:       Date.now()
        };

        try {
            window[callbackName](label, value.trim(), fieldCategory(el), wasEmpty);
        } catch(e) {}
    }

    // ── attach listeners to one element ─────────────────────────────────
    function attachTo(el) {
        snapshotField(el);
        el.addEventListener('change', function() { handleChange(el); }, {passive: true});
        el.addEventListener('blur',   function() { handleChange(el); }, {passive: true});
    }

    // ── scan current DOM ─────────────────────────────────────────────────
    var FIELD_SEL = 'input:not([type=hidden]):not([type=submit]):not([type=button]),' +
                    'select, textarea';

    function scanAndAttach() {
        document.querySelectorAll(FIELD_SEL).forEach(attachTo);
    }

    scanAndAttach();

    // ── watch for SPA-injected fields ────────────────────────────────────
    var observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(m) {
            m.addedNodes.forEach(function(node) {
                if (node.nodeType !== 1) return;
                if (node.matches && node.matches(FIELD_SEL)) attachTo(node);
                if (node.querySelectorAll) {
                    node.querySelectorAll(FIELD_SEL).forEach(attachTo);
                }
            });
        });
    });
    observer.observe(document.body, {childList: true, subtree: true});

    // ── dump helper (called from Python at flush time) ───────────────────
    window.__dumpHumanFillCaptures = function() {
        return JSON.stringify(window.__humanFillCaptures);
    };

})
"""


# --------------------------------------------------------------------------- #
#  Python class                                                                #
# --------------------------------------------------------------------------- #

class HumanFillTracker:
    """
    Attaches to a Playwright page at creation time and captures every human
    field fill or correction for the lifetime of that tab.

    Usage:
        tracker = HumanFillTracker(page, user_id, recorder, site_url)
        await tracker.attach()          # call once after page is ready
        # ... agent runs, state machine ends, browser stays open ...
        # tracker keeps debounce-saving any human fills automatically
        await tracker.flush_now()       # optional immediate flush
    """

    DEBOUNCE_SECONDS = 8

    def __init__(
        self,
        page,
        user_id: Optional[str],
        user_pattern_recorder,
        site_url: str = "",
    ):
        self.page        = page
        self.user_id     = user_id
        self.recorder    = user_pattern_recorder
        self.site_domain = self._extract_domain(site_url)

        self._captures:        Dict[str, Dict[str, Any]] = {}
        self._debounce_task:   Optional[asyncio.Task]    = None
        self._callback_name    = "__pythonHumanFillCb"
        self._attached         = False

    # ---------------------------------------------------------------------- #
    #  Public API                                                              #
    # ---------------------------------------------------------------------- #

    async def attach(self):
        """
        Inject the tracker script into the page.

        Uses two mechanisms so the tracker survives every navigation:
          1. page.add_init_script() — Playwright re-runs this automatically on
             every page load / navigation within the same Page object.
          2. page.evaluate()        — Runs immediately for the current page,
             because add_init_script only fires on future navigations.
        """
        # expose_function persists across navigations on the same Page object.
        try:
            await self.page.expose_function(self._callback_name, self._on_field_changed)
        except Exception as _ef_err:
            # Only truly silent when it's the "already registered" case.
            err_msg = str(_ef_err).lower()
            if "already" not in err_msg and "exist" not in err_msg:
                logger.warning(
                    f"HumanFillTracker: expose_function failed "
                    f"(callback='{self._callback_name}'): {_ef_err}"
                )

        # The init script wraps the tracker in DOMContentLoaded so it's safe
        # to use even when add_init_script fires before document.body exists.
        cb   = self._callback_name
        init_script = f"""
(function() {{
    function runTracker() {{
        window.__humanFillTrackerAttached = false;
        ({_TRACKER_JS})('{cb}');
    }}
    if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', runTracker);
    }} else {{
        runTracker();
    }}
}})();
"""

        # Register for ALL future navigations on this page
        try:
            await self.page.add_init_script(script=init_script)
        except Exception as e:
            logger.warning(f"HumanFillTracker: add_init_script failed: {e}")

        # Also run immediately for the current page
        try:
            await self.page.evaluate(
                f"window.__humanFillTrackerAttached = false; ({_TRACKER_JS})('{cb}');"
            )
            self._attached = True
            logger.info(
                f"HumanFillTracker: Attached to {self.page.url[:60]} "
                f"(user={self.user_id or 'anon'}, domain={self.site_domain})"
            )
        except Exception as e:
            logger.warning(f"HumanFillTracker: Immediate inject failed: {e}")

    async def update_page(self, new_page):
        """
        Switch tracker to a different page object (new tab / popup).
        Re-runs the full attach() so add_init_script and expose_function
        are both registered on the new Page object.
        """
        self.page        = new_page
        self.site_domain = self._extract_domain(new_page.url)
        self._attached   = False
        await self.attach()

    async def flush_now(self):
        """Flush all buffered captures immediately (e.g. on agent shutdown)."""
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
        await self._dump_from_js()
        await self._filter_captures_with_gemini()
        await self._flush_captures()

    # ---------------------------------------------------------------------- #
    #  Internal                                                                #
    # ---------------------------------------------------------------------- #

    _ACTION_LABEL_RE = re.compile(
        r'^(type |enter |click |select |add |search |upload |choose |pick |please |start |begin )',
        re.IGNORECASE
    )
    _NAV_LABEL_RE = re.compile(
        r'^(next|back|previous|submit|cancel|clear|save|continue|ok|done|finish|skip|'
        r'close|apply|update|delete|remove|edit|open|show|hide|toggle|confirm|reset|'
        r'refresh|load|more|less|all|none)$',
        re.IGNORECASE
    )
    _HTML_NAME_RE = re.compile(
        r'^(btn|input|field|el|frm|txt|chk|sel|ddl|rb|cb|lbl|div|span|form|ctrl|val|data|item|row|col)',
        re.IGNORECASE
    )

    def _looks_like_html_name(self, label: str) -> bool:
        if ' ' in label:           return False   # real labels have spaces
        if self._HTML_NAME_RE.match(label): return True
        if '-' in label or '_' in label:    return True   # kebab/snake_case
        if re.search(r'[a-z][A-Z]', label): return True  # camelCase
        return False

    def _is_noisy(self, label: str, value: str) -> bool:
        """
        Python mirror of the JS isNoisyCapture function.
        Applied as a second gate on anything that reaches the Python callback.
        """
        l, v = label.strip(), value.strip()
        ll, vl = l.lower(), v.lower()

        if '\n' in l:                             return True  # autocomplete pollution
        if len(l) > 120:                          return True  # autocomplete pollution
        if len(l) < 2:                            return True  # meaningless
        if l.strip().isdigit():                   return True  # internal index
        if self._looks_like_html_name(l):         return True  # HTML element name
        if self._ACTION_LABEL_RE.match(l):        return True  # UI instruction
        if self._NAV_LABEL_RE.match(ll):          return True  # navigation button
        if ll == vl:                              return True  # option == label
        if vl in ll and re.search(               # autocomplete "X selected"
                r'\b(selected|available|results?|options?|match(?:es)?|found)\b', ll):
            return True
        if 'fakepath' in v.lower():               return True  # file input
        if vl in ('', '-', '--', 'n/a'):          return True  # transient/empty
        return False

    def _on_field_changed(self, label: str, value: str, field_type: str, was_empty: bool):
        """Bridge called from JS on every field change."""
        if not label or not value:
            return

        # File inputs always show C:\fakepath\filename — useless to store
        if field_type == 'file_upload':
            return

        if self._is_noisy(label, value):
            return

        source = "human_fill" if was_empty else "human_correction"
        self._captures[label] = {
            "value":      value,
            "field_type": field_type,
            "source":     source,
            "changed_at": datetime.utcnow(),
        }

        logger.debug(f"HumanFillTracker: [{source}] '{label}' = '{value[:40]}'")
        self._reset_debounce()

    def _reset_debounce(self):
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = asyncio.create_task(self._debounce_flush())

    async def _debounce_flush(self):
        try:
            await asyncio.sleep(self.DEBOUNCE_SECONDS)
            await self._dump_from_js()
            await self._filter_captures_with_gemini()
            await self._flush_captures()
        except asyncio.CancelledError:
            pass

    async def _dump_from_js(self):
        """Pull JS-side buffer to catch any blur-missed fields."""
        try:
            raw = await self.page.evaluate(
                "window.__dumpHumanFillCaptures ? window.__dumpHumanFillCaptures() : '{}'"
            )
            js_captures = json.loads(raw or "{}")
            for label, data in js_captures.items():
                if label not in self._captures and data.get("value"):
                    if data.get("type") == "file_upload":
                        continue
                    was_empty = data.get("wasEmpty", False)
                    self._captures[label] = {
                        "value":      data["value"],
                        "field_type": data.get("type", "text_input"),
                        "source":     "human_fill" if was_empty else "human_correction",
                        "changed_at": datetime.utcnow(),
                    }
        except Exception as e:
            logger.debug(f"HumanFillTracker: JS dump skipped: {e}")

    # Max chars of the actual value to include in the filter prompt.
    # Long enough for Gemini to judge coherence; short enough to avoid
    # sending full essays unnecessarily.
    _FILTER_VALUE_PREVIEW = 120

    async def _filter_captures_with_gemini(self) -> None:
        """
        ONE Gemini call that classifies every accumulated capture as KEEP or
        DISCARD before anything is written to the database.

        What is sent to Gemini
        ----------------------
        For each capture:
          - field label  (the visible form question / label text)
          - field type   (text_input, textarea, dropdown, …)
          - value preview — first _FILTER_VALUE_PREVIEW chars of the value

        The preview lets Gemini verify that BOTH the label AND the value make
        sense as a pair. A noisy/incorrect label extraction (e.g. autocomplete
        tooltip text that ended up as the label) will be visible because the
        label won't match the value. Gemini is explicitly instructed to
        DISCARD any entry where either side looks wrong.

        Fallback
        --------
        If the Gemini call fails (API error, timeout, not configured), the
        method falls back to a heuristic: discard any capture whose value
        is longer than MAX_VALUE_LEN characters.

        Timing
        ------
        Called ONCE per flush window (after the 8-second debounce expires or
        flush_now() is called). All accumulated captures travel in ONE call.
        """
        if not self._captures:
            return

        MAX_VALUE_LEN = 200   # heuristic fallback threshold

        # ── Build the prompt items ─────────────────────────────────────────────
        labels = list(self._captures.keys())
        items  = []
        for i, label in enumerate(labels, 1):
            data    = self._captures[label]
            value   = data.get("value", "")
            f_type  = data.get("field_type", "text_input")
            preview = value[:self._FILTER_VALUE_PREVIEW]
            if len(value) > self._FILTER_VALUE_PREVIEW:
                preview += "…"
            items.append(
                f'{i}. label="{label}" | type={f_type} | value="{preview}"'
            )

        prompt = (
            "You are a data quality filter for a job-application autofill system.\n"
            "For each captured form field decide: KEEP or DISCARD.\n\n"
            "Rules — KEEP when ALL of the following are true:\n"
            "  1. The label looks like a real form field question (not a UI tooltip,\n"
            "     autocomplete suggestion, internal element name, or button text).\n"
            "  2. The value looks like a genuine answer to that label\n"
            "     (the label and value make sense together as a question-answer pair).\n"
            "  3. The information is a stable personal fact that would be valid on\n"
            "     a DIFFERENT job application at a DIFFERENT company — e.g. name,\n"
            "     phone, email, LinkedIn, city, GPA, supervisor name, short factual\n"
            "     answers, pronounciation notes, preferred name, etc.\n\n"
            "Rules — DISCARD when ANY of the following is true:\n"
            "  • The label looks garbled, too long, or like a UI hint / button / tooltip.\n"
            "  • The value does not match what the label is asking for.\n"
            "  • The answer is job-specific (why this role/company, mission alignment,\n"
            "     cover letter, company-specific essay, narrative > a few sentences).\n"
            "  • The value is clearly a transient UI state (e.g. partial address from\n"
            "     an autocomplete dropdown before the user confirmed it).\n\n"
            "Items:\n"
            + "\n".join(items)
            + "\n\nReply with ONLY lines in this exact format (one per item, no extra text):\n"
            "1: KEEP\n2: DISCARD\n…"
        )

        discard_indices: set = set()

        try:
            import os
            from gemini_compat import genai

            model  = genai.GenerativeModel("gemini-2.0-flash")
            config = genai.GenerationConfig(temperature=0.0, max_output_tokens=256)

            import asyncio as _asyncio
            resp   = await _asyncio.to_thread(
                model.generate_content, prompt, generation_config=config
            )
            text   = (resp.text or "").strip()

            for line in text.splitlines():
                line = line.strip()
                if not line or ':' not in line:
                    continue
                parts = line.split(':', 1)
                try:
                    idx = int(parts[0].strip()) - 1
                    verdict = parts[1].strip().upper()
                    if verdict == 'DISCARD' and 0 <= idx < len(labels):
                        discard_indices.add(idx)
                except (ValueError, IndexError):
                    continue

            if discard_indices:
                discarded = [labels[i] for i in sorted(discard_indices)]
                logger.info(
                    f"HumanFillTracker: Gemini filter — discarding "
                    f"{len(discard_indices)}/{len(labels)} captures: "
                    + ", ".join(f'"{l}"' for l in discarded)
                )
                for i in sorted(discard_indices, reverse=True):
                    self._captures.pop(labels[i], None)
            else:
                logger.info(
                    f"HumanFillTracker: Gemini filter — all {len(labels)} captures approved"
                )

        except Exception as e:
            logger.warning(
                f"HumanFillTracker: Gemini filter failed ({e}), "
                f"using heuristic fallback (max {MAX_VALUE_LEN} chars)"
            )
            # Fallback: discard long values (essays)
            for label, data in list(self._captures.items()):
                if len(data.get("value", "")) > MAX_VALUE_LEN:
                    logger.debug(
                        f"HumanFillTracker: Heuristic discard '{label}' "
                        f"(value length {len(data.get('value', ''))})"
                    )
                    self._captures.pop(label)

    async def _flush_captures(self):
        if not self._captures:
            return

        saved = 0
        for label, data in list(self._captures.items()):
            try:
                await self.recorder.record_human_fill(
                    field_label   = label,
                    field_value   = data["value"],
                    field_category= data["field_type"],
                    source        = data["source"],
                    was_ai_attempted = True,
                    user_id       = self.user_id,
                    site_domain   = self.site_domain,
                )
                saved += 1
            except Exception as e:
                logger.error(f"HumanFillTracker: Save failed for '{label}': {e}")

        logger.info(f"HumanFillTracker: Saved {saved}/{len(self._captures)} fills.")
        self._captures.clear()

    @staticmethod
    def _extract_domain(url: str) -> Optional[str]:
        if not url:
            return None
        m = re.search(r'https?://([^/]+)', url)
        if m:
            parts = m.group(1).split('.')
            return '.'.join(parts[-2:]) if len(parts) >= 2 else m.group(1)
        return None
