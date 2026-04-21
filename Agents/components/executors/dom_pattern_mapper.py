"""
DomPatternMapper — resolves a form field's Playwright Locator using previously
recorded DOM structural patterns, WITHOUT calling Gemini.

Usage example:
    mapper = DomPatternMapper(page, site_url="https://boards.greenhouse.io/acme")
    locator = await mapper.find_field("First Name")
    if locator:
        await locator.fill("John")
    else:
        # fall back to Gemini / existing detection logic
        pass

After a successful fill from ANY source, record the structural pattern:
    mapper.recorder.record(
        site_domain=site_url,
        label_text="First Name",
        relationship="label_for",
        field_type="text",
        extra={"css_selector": "#first_name_input"}
    )
"""
import re
from typing import Any, Dict, Optional, Union
from playwright.async_api import Frame, Locator, Page
from loguru import logger

from .dom_pattern_recorder import DomPatternRecorder


class DomPatternMapper:
    """Attempts to locate a form field Locator using recorded DOM patterns.

    When a pattern is found and resolves successfully, the agent can fill the
    field without any external AI call.
    """

    def __init__(
        self,
        page: Union[Page, Frame],
        site_url: str = "",
        recorder: Optional[DomPatternRecorder] = None,
    ):
        self.page = page
        self.site_url = site_url
        self.recorder = recorder or DomPatternRecorder()

    async def find_field(self, label_text: str) -> Optional[Locator]:
        """Try to locate the input field for a given label using saved DOM patterns.

        Returns:
            A visible Playwright Locator, or None if pattern lookup fails.
        """
        pattern = self.recorder.get(self.site_url, label_text)
        if not pattern:
            logger.debug(f"DomPatternMapper: no saved pattern for '{label_text}' on {self.site_url}")
            return None

        relationship = pattern.get("relationship", "")
        extra = pattern.get("extra") or {}

        logger.info(
            f"DomPatternMapper: applying saved DOM pattern for '{label_text}' "
            f"→ relationship={relationship} on {self.site_url}"
        )

        try:
            locator = await self._resolve(label_text, relationship, extra, pattern)
            if locator and await locator.count() > 0:
                logger.info(f"✅ DomPatternMapper: resolved '{label_text}' via {relationship}")
                return locator
            else:
                logger.warning(
                    f"DomPatternMapper: pattern '{relationship}' for '{label_text}' "
                    "resolved but element not found — recording failure"
                )
                self.recorder.record(
                    self.site_url, label_text, relationship,
                    field_type=pattern.get("field_type", "text"), success=False
                )
                return None
        except Exception as e:
            logger.warning(f"DomPatternMapper: resolution error for '{label_text}': {e}")
            self.recorder.record(
                self.site_url, label_text, relationship,
                field_type=pattern.get("field_type", "text"), success=False
            )
            return None

    async def _resolve(
        self,
        label_text: str,
        relationship: str,
        extra: Dict[str, Any],
        pattern: Dict[str, Any],
    ) -> Optional[Locator]:
        """Dispatch to the correct resolution strategy based on relationship type."""
        if relationship == "label_for":
            return await self._resolve_label_for(label_text, extra)

        if relationship == "aria_labelledby":
            return await self._resolve_aria_labelledby(label_text, extra)

        if relationship == "aria_label":
            return await self._resolve_aria_label(label_text)

        if relationship == "ancestor_walk":
            return await self._resolve_ancestor_walk(label_text, extra)

        if relationship == "parent_label":
            return await self._resolve_parent_label(label_text)

        if relationship == "placeholder":
            return await self._resolve_placeholder(extra.get("placeholder_text", label_text))

        if relationship == "custom_class":
            return await self._resolve_custom_class(extra)

        logger.debug(f"DomPatternMapper: unknown relationship '{relationship}'")
        return None

    async def _resolve_label_for(self, label_text: str, extra: Dict[str, Any]) -> Optional[Locator]:
        """<label for="X">label_text</label>  →  input#X"""
        # If we have the exact 'for' attribute value saved, use it directly
        for_value = extra.get("label_for_value") or extra.get("css_selector", "").lstrip("#")
        if for_value:
            loc = self.page.locator(f"#{for_value}").first
            if await loc.count() > 0:
                return loc

        # Otherwise, find the label by text and then follow `for` attribute
        label_loc = self.page.locator(f'label:has-text("{label_text}")').first
        if await label_loc.count() > 0:
            for_attr = await label_loc.get_attribute("for")
            if for_attr:
                loc = self.page.locator(f"#{for_attr}").first
                if await loc.count() > 0:
                    return loc
        return None

    async def _resolve_aria_labelledby(self, label_text: str, extra: Dict[str, Any]) -> Optional[Locator]:
        """input[aria-labelledby="X"] where element #X has label_text."""
        label_id = extra.get("labelledby_id")
        if label_id:
            loc = self.page.locator(f'[aria-labelledby="{label_id}"]').first
            if await loc.count() > 0:
                return loc

        # Search for any element with this labelledby text
        result = await self.page.evaluate(f"""
            () => {{
                const norm = {json_dumps_str(label_text.lower())};
                for (const el of document.querySelectorAll('[aria-labelledby]')) {{
                    const ids = el.getAttribute('aria-labelledby').split(/\\s+/);
                    for (const id of ids) {{
                        const labelEl = document.getElementById(id);
                        if (labelEl && labelEl.textContent.trim().toLowerCase().startsWith(norm)) {{
                            if (!el.id) el.setAttribute('data-dwpmap', 'resolved');
                            return el.id || 'data-attr';
                        }}
                    }}
                }}
                return null;
            }}
        """)
        if result and result != 'data-attr':
            loc = self.page.locator(f"#{result}").first
            if await loc.count() > 0:
                return loc
        if result == 'data-attr':
            loc = self.page.locator('[data-dwpmap="resolved"]').first
            if await loc.count() > 0:
                return loc
        return None

    async def _resolve_aria_label(self, label_text: str) -> Optional[Locator]:
        """[aria-label="label_text"] direct match."""
        safe = label_text.replace('"', '\\"')
        for tag in ("input", "select", "textarea", "[role='combobox']"):
            loc = self.page.locator(f'{tag}[aria-label="{safe}"]').first
            if await loc.count() > 0:
                return loc
        # Case-insensitive partial match
        loc = self.page.locator(f'[aria-label*="{safe}" i]').first
        if await loc.count() > 0:
            return loc
        return None

    async def _resolve_ancestor_walk(self, label_text: str, extra: Dict[str, Any]) -> Optional[Locator]:
        """Walk DOM: find container that has label_text, then take its input."""
        container_sel = extra.get("container_selector", "")
        safe = label_text.replace("'", "\\'")
        js = f"""
            () => {{
                function getText(el) {{
                    return (el.innerText || el.textContent || '').trim().toLowerCase();
                }}
                const targets = Array.from(document.querySelectorAll(
                    '{container_sel or "div, fieldset, li, section"}'
                ));
                for (const container of targets) {{
                    if (getText(container).includes('{safe.lower()}')) {{
                        const field = container.querySelector('input, select, textarea');
                        if (field) {{
                            if (!field.id) field.setAttribute('data-dwpmap', 'ancestor');
                            return field.id || null;
                        }}
                    }}
                }}
                return null;
            }}
        """
        field_id = await self.page.evaluate(js)
        if field_id:
            loc = self.page.locator(f"#{field_id}").first
            if await loc.count() > 0:
                return loc
        loc = self.page.locator('[data-dwpmap="ancestor"]').first
        if await loc.count() > 0:
            return loc
        return None

    async def _resolve_parent_label(self, label_text: str) -> Optional[Locator]:
        """Field is a direct child of <label>label_text</label>."""
        safe = label_text.replace('"', '\\"')
        loc = self.page.locator(f'label:has-text("{safe}") input, '
                                f'label:has-text("{safe}") select, '
                                f'label:has-text("{safe}") textarea').first
        if await loc.count() > 0:
            return loc
        return None

    async def _resolve_placeholder(self, placeholder_text: str) -> Optional[Locator]:
        """Field identified by placeholder attribute."""
        safe = placeholder_text.replace('"', '\\"')
        loc = self.page.locator(f'[placeholder="{safe}"], [placeholder*="{safe}" i]').first
        if await loc.count() > 0:
            return loc
        return None

    async def _resolve_custom_class(self, extra: Dict[str, Any]) -> Optional[Locator]:
        """Use a saved CSS selector directly."""
        css = extra.get("css_selector", "")
        if not css:
            return None
        loc = self.page.locator(css).first
        if await loc.count() > 0:
            return loc
        return None

    # ── Helper for recording a newly discovered pattern ───────────────────────

    def record_successful_pattern(
        self,
        label_text: str,
        relationship: str,
        field_type: str = "text",
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Call this after successfully locating a field to persist the pattern."""
        self.recorder.record(
            site_domain=self.site_url,
            label_text=label_text,
            relationship=relationship,
            field_type=field_type,
            success=True,
            extra=extra,
        )

    def record_failed_pattern(
        self,
        label_text: str,
        relationship: str,
        field_type: str = "text",
    ) -> None:
        """Call this when a previously saved pattern fails to locate the field."""
        self.recorder.record(
            site_domain=self.site_url,
            label_text=label_text,
            relationship=relationship,
            field_type=field_type,
            success=False,
        )


def json_dumps_str(s: str) -> str:
    """Return a JSON-safe double-quoted string literal for embedding in JS."""
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
