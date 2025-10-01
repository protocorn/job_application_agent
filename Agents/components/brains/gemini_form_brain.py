import os
import json
import re
from typing import Any, Dict, List, Optional
from loguru import logger
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

class GeminiFormBrain:
    """Uses Gemini Pro Vision to analyze a form screenshot and identify all actionable elements."""

    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            logger.error("GEMINI_API_KEY environment variable not set. This brain will be disabled.")
            self.model = None
            return
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel('gemini-pro-vision') # Vision model is required for image analysis
        logger.info("✅ Gemini Form Brain configured successfully.")

    async def analyze_screenshot(self, screenshot_bytes: bytes, profile_keys: List[str]) -> Optional[Dict[str, Any]]:
        """
        Analyzes a screenshot to identify all actionable elements for filling a form.

        Args:
            screenshot_bytes: The PNG screenshot as bytes.
            profile_keys: The keys from the user's profile that still need to be filled.

        Returns:
            A dictionary containing lists of identified buttons and fields, or None on failure.
        """
        if not self.model:
            return None # Return early if the model isn't configured

        logger.info("🧠 Gemini Form Brain is analyzing the screenshot...")
        image_part = {"mime_type": "image/png", "data": screenshot_bytes}
        
        prompt = self._create_analysis_prompt(profile_keys)

        try:
            response = await self.model.generate_content_async([prompt, image_part])
            actionable_elements = self._parse_json_response(response.text)
            
            if actionable_elements:
                logger.info("🧠 Gemini identified actionable elements from the screenshot.")
                logger.debug(f"AI Analysis Result: {actionable_elements}")
            else:
                logger.warning("🧠 Gemini could not identify any actionable elements from the screenshot.")

            return actionable_elements

        except Exception as e:
            logger.error(f"❌ Error calling Gemini Vision API: {e}")
            return None

    def _create_analysis_prompt(self, profile_keys: List[str]) -> str:
        """Creates a detailed, structured prompt for the Vision API."""
        
        # This prompt is more robust as it asks the AI to perform a single, focused task: identification.
        return f"""
        You are an expert UI analyst for an automated job application tool. Your task is to analyze the provided screenshot of a web page and identify all actionable elements relevant to filling out a form.

        The user's remaining profile data fields are: {', '.join(profile_keys)}.

        TASK:
        Examine the screenshot and identify two types of elements:
        1.  **Action Buttons**: Any buttons that represent a primary action.
        2.  **Form Fields**: Any empty and visible input fields that need to be filled.

        RESPONSE FORMAT:
        Return ONLY a single, valid JSON object with two keys: "buttons" and "fields".
        - "buttons": A list of objects, where each object represents a button. Include the button's exact text and a short, descriptive type from this list: ["autofill", "upload_resume", "next_step", "submit_application", "other"].
        - "fields": A list of objects, where each object represents an empty form field. Include the field's visible label text and try to map it to one of the available profile keys.

        EXAMPLE RESPONSE:
        {{
          "buttons": [
            {{ "text": "Autofill with Resume", "type": "autofill" }},
            {{ "text": "Next Step", "type": "next_step" }}
          ],
          "fields": [
            {{ "label": "First Name", "profile_key": "first_name" }},
            {{ "label": "Email Address", "profile_key": "email" }}
          ]
        }}

        RULES:
        - Be precise. Only include elements you can clearly see in the image.
        - If you see no relevant elements, return empty lists for "buttons" and "fields".
        - Do not invent elements or guess their labels.
        """

    def _parse_json_response(self, response_text: str) -> Optional[Dict[str, Any]]:
        """A more robust method to find and parse a JSON object from the AI's response text."""
        try:
            # Use a regex to find a JSON object embedded within markdown or other text
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if json_match:
                json_text = json_match.group(1)
            else:
                # As a fallback, try to find the first '{' and last '}'
                start = response_text.find('{')
                end = response_text.rfind('}') + 1
                if start != -1 and end != 0:
                    json_text = response_text[start:end]
                else:
                    logger.warning("No valid JSON object found in the AI response.")
                    return None
            
            return json.loads(json_text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from Gemini response: {e}")
            logger.debug(f"Raw response text: {response_text}")
            return None