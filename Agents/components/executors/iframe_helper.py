import logging
from typing import Optional
from playwright.async_api import Page, Frame

logger = logging.getLogger(__name__)

class IframeHelper:
    """Utilities for detecting and selecting actionable iframes."""

    def __init__(self, page: Page):
        self.page = page

    async def find_actionable_frame(self) -> Optional[Frame]:
        """
        Returns a Frame that likely contains interactive content (forms or buttons).
        Preference order:
        1) Frame whose URL or id hints common ATS providers (greenhouse, lever, workday)
        2) Frame containing visible form fields or submit/next buttons
        """
        try:
            candidate_frames = self.page.frames
            if not candidate_frames or len(candidate_frames) == 0:
                return None

            # Heuristic 1: Known ATS providers in URL or id/name
            preferred_keywords = [
                "greenhouse", "grnhse", "job_app", "lever", "workday", "icims", "ashby"
            ]

            prioritized: list[Frame] = []
            others: list[Frame] = []

            for frame in candidate_frames:
                frame_url = (frame.url or "").lower()
                frame_name = (frame.name or "").lower()
                if any(k in frame_url or k in frame_name for k in preferred_keywords):
                    prioritized.append(frame)
                else:
                    others.append(frame)

            ordered = prioritized + others

            # Heuristic 2: Check for visible interactive elements
            for frame in ordered:
                try:
                    # Quick presence checks with short timeouts
                    has_inputs = await frame.locator('input:not([type="hidden"]), select, textarea').first.is_visible(timeout=500)
                    if has_inputs:
                        return frame
                except Exception:
                    pass
                try:
                    has_buttons = await frame.get_by_role("button").first.is_visible(timeout=500)
                    if has_buttons:
                        return frame
                except Exception:
                    pass

            # Nothing clearly actionable
            return None
        except Exception as e:
            logger.debug(f"Iframe detection failed: {e}")
            return None


