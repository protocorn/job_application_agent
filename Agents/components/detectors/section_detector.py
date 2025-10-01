import re
from typing import Dict, List, Optional, Any
from playwright.async_api import Page, Locator
from loguru import logger

class SectionDetector:
    """Detects repeatable sections (like education/work) and finds their 'Add' buttons."""

    # --- Centralized Configuration ---
    # Consolidating patterns into a single, easy-to-manage dictionary.
    # This makes the class much easier to extend with new section types.
    SECTION_PATTERNS: Dict[str, Dict[str, List[str]]] = {
        'education': {
            'container_keywords': [
                r'education', r'academic background', r'school history', r'university'
            ],
            'add_button_keywords': [
                r'add education', r'add another degree', r'add school', r'add academic'
            ],
        },
        'work_experience': {
            'container_keywords': [
                r'work experience', r'employment history', r'professional experience', r'job history', r'career history'
            ],
            'add_button_keywords': [
                r'add work', r'add another experience', r'add employment', r'add job'
            ],
        }
    }

    def __init__(self, page: Page):
        self.page = page

    async def detect(self, section_type: str) -> Optional[Dict[str, Locator]]:
        """
        A generic method to detect a section container and its 'Add' button. This
        replaces the duplicated `detect_education_section` and `detect_work_experience_section`
        methods.

        Args:
            section_type: The key for the section to detect (e.g., 'education', 'work_experience').

        Returns:
            A dictionary with 'container' and 'add_button' Locators, or None if not found.
        """
        logger.info(f"🔎 Detecting '{section_type}' section...")
        
        patterns = self.SECTION_PATTERNS.get(section_type)
        if not patterns:
            logger.warning(f"No patterns defined for section type: '{section_type}'")
            return None

        # Step 1: Find a robust container for the entire section. This is a stronger
        # approach than just finding any text match on the page.
        container = await self._find_section_container(patterns['container_keywords'])
        if not container:
            logger.info(f"No '{section_type}' section container found.")
            return None

        # Step 2: Find the 'Add' button *within* that specific container. This is a crucial
        # improvement that prevents finding an incorrect "Add" button from another section.
        add_button = await self._find_add_button_in_container(container, patterns['add_button_keywords'])
        if not add_button:
            # It's possible a section exists but has no add button (e.g., if it's pre-filled)
            logger.info(f"Found a '{section_type}' section but it has no 'Add' button.")
            return None 

        logger.success(f"✅ Detected '{section_type}' section and its 'Add' button.")
        return {
            'type': section_type,
            'container': container,
            'add_button': add_button,
        }

    async def _find_section_container(self, keywords: List[str]) -> Optional[Locator]:
        """
        Finds the best container element for a section by locating a header
        and then traversing up to find its parent grouping element.
        """
        keyword_regex = re.compile("|".join(keywords), re.IGNORECASE)
        
        # Efficiently find a relevant header using a single regex.
        header_locator = self.page.get_by_role("heading", name=keyword_regex).first
        
        if await header_locator.is_visible(timeout=1500):
            # The key improvement: After finding a header, find its logical parent container.
            # This establishes a reliable boundary for the section.
            try:
                # This XPath looks for the closest ancestor that is a <section>, <fieldset>,
                # or a <div> that is styled or marked as a group.
                container = header_locator.locator(
                    'xpath=ancestor::section | ancestor::fieldset | ancestor::div[contains(@class, "section")] | ancestor::div[contains(@class, "group")]'
                ).first
                if await container.count() > 0 and await container.is_visible():
                     return container
                # Fallback to the direct parent if a more semantic container isn't found.
                return header_locator.locator('xpath=..')
            except Exception:
                 return header_locator.locator('xpath=..') # Fallback on error
        
        return None

    async def _find_add_button_in_container(self, container: Locator, keywords: List[str]) -> Optional[Locator]:
        """
        Finds the first visible 'Add' button *only within* the provided container element.
        """
        keyword_regex = re.compile("|".join(keywords), re.IGNORECASE)
        
        # By using container.get_by_role, we scope the search, making it faster and more accurate.
        add_button = container.get_by_role("button", name=keyword_regex).first
        
        if await add_button.is_visible(timeout=1500):
            return add_button
            
        return None