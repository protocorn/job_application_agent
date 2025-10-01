import base64
import json
import re
import os
from typing import Dict, Any, Optional
from io import BytesIO
from playwright.async_api import Page, Error
from loguru import logger
import google.generativeai as genai
from PIL import Image
from dotenv import load_dotenv

load_dotenv()

class GeminiPageAnalyzer:
    """Uses Gemini Vision to classify unknown pages and determine the next strategic action."""

    def __init__(self):
        self.model = None
        self._configure_gemini()

    def _configure_gemini(self):
        """Configure Gemini API using environment variables."""
        try:
            api_key = os.getenv('GEMINI_API_KEY')
            if not api_key:
                logger.warning("⚠️ GEMINI_API_KEY not found. Page Analyzer will run in fallback mode.")
                return
            genai.configure(api_key=api_key)
            # Use Gemini 2.0 Flash which supports image input
            self.model = genai.GenerativeModel("gemini-2.0-flash")
            logger.info("✅ Gemini Page Analyzer configured successfully.")
        except Exception as e:
            logger.error(f"❌ Failed to configure Gemini Page Analyzer: {e}")

    async def analyze_page(self, page: Page) -> Dict[str, Any]:
        """
        Analyzes the current page using a screenshot and suggests the next action.

        Returns:
            A dictionary containing the page type, confidence, and suggested next action.
        """
        if not self.model:
            return self._fallback_analysis(page)

        logger.info("🧠 Analyzing unknown page with Gemini Vision...")
        try:
            screenshot_bytes = await page.screenshot()
            image = Image.open(BytesIO(screenshot_bytes))
            prompt = self._create_analysis_prompt()

            # Pass image and prompt as a list
            response = await self.model.generate_content_async([image, prompt])
            ai_result = self._parse_json_response(response.text)

            if not ai_result:
                logger.warning("AI analysis failed to produce a valid result. Using fallback.")
                return await self._fallback_analysis(page)

            # The Python code, not the AI, determines the next action based on the type.
            page_type = ai_result.get("page_type", "OTHER")
            next_action = self._get_next_action_from_type(page_type)
            
            result = {
                "page_type": page_type,
                "confidence": ai_result.get("confidence", 0.0),
                "reason": ai_result.get("reason", "No reason provided by AI."),
                "next_action": next_action,
                "source": "ai_analysis"
            }
            logger.success(f"🧠 AI Page Analysis Result: {result['page_type']} (Action: {result['next_action']})")
            return result

        except Exception as e:
            logger.error(f"❌ AI page analysis failed with an exception: {e}")
            return await self._fallback_analysis(page)

    def _create_analysis_prompt(self) -> str:
        """Creates a focused prompt asking the AI only to classify the page."""
        return """
        You are an expert UI analyst for a web automation agent. Your task is to analyze the provided screenshot and classify the type of web page it is.

        Based on the visual layout, text, and form elements, classify the page into ONE of the following types:
        - JOB_LISTING: A page describing a single job, which should contain an "Apply" button.
        - APPLICATION_FORM: A form for entering personal details, work history, etc.
        - AUTHENTICATION: A page asking the user to log in, sign up, or register.
        - SUCCESS_CONFIRMATION: A page confirming that an action (like submitting an application) was successful.
        - ERROR_PAGE: A page showing an error (e.g., 404 Not Found, Application Error).
        - OTHER: Any other type of page (e.g., a company homepage, a news article).

        RESPONSE FORMAT:
        Return ONLY a single, valid JSON object with three keys:
        - "page_type": (string) Your classification from the list above.
        - "confidence": (float) Your confidence in this classification, from 0.0 to 1.0.
        - "reason": (string) A brief explanation of your reasoning.

        EXAMPLE:
        {
          "page_type": "APPLICATION_FORM",
          "confidence": 0.95,
          "reason": "The page contains multiple input fields for personal information like 'First Name', 'Last Name', and 'Email', which is characteristic of a job application form."
        }
        """

    def _parse_json_response(self, response_text: str) -> Optional[Dict[str, Any]]:
        """A robust method to find and parse a JSON object from the AI's response text."""
        try:
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if json_match:
                json_text = json_match.group(1)
            else:
                start = response_text.find('{')
                end = response_text.rfind('}') + 1
                if start == -1 or end == 0: return None
                json_text = response_text[start:end]
            
            return json.loads(json_text)
        except (json.JSONDecodeError, IndexError) as e:
            logger.error(f"Failed to parse JSON from Gemini response: {e}")
            return None

    def _get_next_action_from_type(self, page_type: str) -> str:
        """Determines the appropriate next action based on the classified page type."""
        action_map = {
            "JOB_LISTING": "find_and_click_apply",
            "APPLICATION_FORM": "fill_form",
            "AUTHENTICATION": "handle_authentication",
            "SUCCESS_CONFIRMATION": "process_success",
            "ERROR_PAGE": "report_error_and_stop",
        }
        return action_map.get(page_type, "human_intervention_required")

    async def _fallback_analysis(self, page: Page) -> Dict[str, Any]:
        """A simple, rule-based fallback analysis when the AI is unavailable."""
        logger.warning("Using fallback page analysis.")
        try:
            url = page.url.lower()
            title = await page.title()
            
            if "apply" in url or "application" in url:
                page_type = "APPLICATION_FORM"
            elif "login" in url or "auth" in url:
                page_type = "AUTHENTICATION"
            elif "job" in url or "career" in url or "position" in title:
                page_type = "JOB_LISTING"
            else:
                page_type = "OTHER"

            next_action = self._get_next_action_from_type(page_type)
            return {
                "page_type": page_type,
                "confidence": 0.4, # Lower confidence for fallback
                "reason": "Determined by simple URL and title keyword matching.",
                "next_action": next_action,
                "source": "fallback_analysis"
            }
        except Error as e:
            return {
                "page_type": "ERROR_PAGE",
                "confidence": 0.9,
                "reason": f"Could not analyze page due to an error: {e}",
                "next_action": "report_error_and_stop",
                "source": "fallback_error"
            }