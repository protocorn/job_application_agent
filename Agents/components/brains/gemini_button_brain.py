import json
import re
from typing import Dict, List, Optional, Any
from loguru import logger
import google.generativeai as genai
import os

class GeminiButtonBrain:
    """AI-powered button detection using text-based Gemini input for better reliability."""
    
    def __init__(self):
        self.model_name = "gemini-2.0-flash"
        self._configure_gemini()
    
    def _configure_gemini(self):
        """Configure Gemini API with the key from token.json."""
        try:
            # Look for token.json in the Agents directory
            token_path = os.path.join(os.path.dirname(__file__), '..', 'token.json')
            if os.path.exists(token_path):
                with open(token_path, 'r') as f:
                    token_data = json.load(f)
                    api_key = token_data.get('gemini_api_key')
                    if api_key:
                        genai.configure(api_key=api_key)
                        logger.info("✅ Gemini Button Brain configured successfully")
                        return
            
            # Fallback to environment variable
            api_key = os.getenv('GEMINI_API_KEY')
            if api_key:
                genai.configure(api_key=api_key)
                logger.info("✅ Gemini Button Brain configured from environment")
            else:
                logger.warning("⚠️ No Gemini API key found. Button detection will be disabled.")
                
        except Exception as e:
            logger.error(f"❌ Failed to configure Gemini Button Brain: {e}")
    
    async def _find_button(self, page_text: str, button_type: str, context: str = "") -> Optional[Dict[str, Any]]:
        """
        Find a specific type of button using text-based analysis.
        
        Args:
            page_text: The HTML content of the page
            button_type: Type of button to find ('apply', 'next', 'submit')
            context: Additional context about what we're looking for
            
        Returns:
            Dictionary with button information or None if not found
        """
        try:
            if not page_text:
                return None
            
            # Extract button elements from HTML
            button_elements = self._extract_button_elements(page_text)
            if not button_elements:
                logger.warning(f"No button elements found on page for {button_type} detection")
                return None
            
            # Create prompt for Gemini
            prompt = self._create_button_detection_prompt(button_elements, button_type, context)
            
            # Query Gemini
            model = genai.GenerativeModel(self.model_name)
            response = model.generate_content(prompt)
            
            # Parse response
            result = self._parse_button_response(response.text, button_type)
            
            if result and result.get('found'):
                logger.info(f"🧠 AI found {button_type} button: {result.get('text', 'Unknown')}")
                return result
            else:
                logger.info(f"🧠 AI could not find {button_type} button")
                return None
                
        except Exception as e:
            logger.error(f"❌ Gemini button detection failed: {e}")
            return None

    async def find_button(self, page_text: str, button_type: str, context: str = "") -> Optional[Dict[str, Any]]:
        """Backward-compatible wrapper that delegates to _find_button."""
        return await self._find_button(page_text, button_type, context)
    
    def _extract_button_elements(self, page_text: str) -> List[Dict[str, Any]]:
        """Extract all button-like elements from HTML text."""
        button_elements: List[Dict[str, Any]] = []

        # Consolidated regex for button-like elements
        button_regex = (
            r'<(?P<tag>button|a|input|div|span)\s+(?P<attrs>[^>]*?)\s*>'
            r'(?P<content>.*?)</(?P=tag)>'
            r'|<(?P<tag_input>input)\s+(?P<attrs_input>[^>]*?)>'
        )

        for match in re.finditer(button_regex, page_text, re.IGNORECASE | re.DOTALL):
            full_element = match.group(0)
            tag = match.group('tag') if match.group('tag') else match.group('tag_input')
            text_content = match.group('content') if match.group('content') else ""

            attributes = self._extract_attributes(full_element)

            button_elements.append({
                'element': full_element,
                'text': text_content.strip(),
                'attributes': attributes,
                'tag': tag.lower() if tag else 'div'
            })

        return button_elements
    
    def _extract_attributes(self, element: str) -> Dict[str, str]:
        """Extract attributes from HTML element."""
        
        attributes = {}
        
        # Find all attribute="value" or attribute='value' patterns
        attr_pattern = r'(\w+)=["\']([^"\']*)["\']'
        matches = re.finditer(attr_pattern, element, re.IGNORECASE)
        
        for match in matches:
            attr_name = match.group(1).lower()
            attr_value = match.group(2)
            attributes[attr_name] = attr_value
        
        return attributes
    
    def _create_button_detection_prompt(self, button_elements: List[Dict[str, Any]], button_type: str, context: str) -> str:
        """Create a prompt for Gemini to detect specific button types."""
        
        # Convert button elements to text for Gemini
        elements_text = []
        for i, element in enumerate(button_elements):
            element_info = f"Element {i+1}:\n"
            element_info += f"  Tag: {element['tag']}\n"
            element_info += f"  Text: '{element['text']}'\n"
            element_info += f"  Attributes: {json.dumps(element['attributes'], indent=2)}\n"
            element_info += f"  Full HTML: {element['element'][:200]}...\n"
            elements_text.append(element_info)
        
        elements_text_str = "\n".join(elements_text)
        
        # Define button type specific instructions
        button_instructions = {
            'apply': {
                'description': 'Apply button - used to start a job application process',
                'keywords': ['apply', 'application', 'apply now', 'start application', 'begin application'],
                'exclude_keywords': ['submit', 'next', 'continue', 'save', 'cancel', 'close']
            },
            'next': {
                'description': 'Next/Continue button - used to proceed to the next step in a multi-page form',
                'keywords': ['next', 'continue', 'proceed', 'next step', 'save and continue', 'continue application'],
                'exclude_keywords': ['apply', 'submit', 'finish', 'complete', 'cancel', 'close']
            },
            'submit': {
                'description': 'Submit button - used to submit/finish a form or application',
                'keywords': ['submit', 'finish', 'complete', 'submit application', 'complete application', 'finish application', 'i agree and submit'],
                'exclude_keywords': ['apply', 'next', 'continue', 'save', 'cancel', 'close']
            }
        }
        
        instruction = button_instructions.get(button_type, button_instructions['apply'])
        
        prompt = f"""
You are an expert web automation assistant. I need you to find a {button_type} button from the following HTML elements.

CONTEXT: {context}

BUTTON TYPE: {instruction['description']}
KEYWORDS TO LOOK FOR: {', '.join(instruction['keywords'])}
KEYWORDS TO AVOID: {', '.join(instruction['exclude_keywords'])}

HTML ELEMENTS TO ANALYZE:
{elements_text_str}

TASK:
1. Look through each element and identify which one is most likely to be a {button_type} button
2. Consider the text content, attributes (especially aria-label, data attributes, class names), and context
3. Prioritize elements that contain the keywords and avoid elements with exclude keywords
4. Look for elements that are likely to be clickable (buttons, links with button role, etc.)
5. When creating the "selector", follow this priority:
   - If the element has a unique 'id', use it (e.g., "#unique-id").
   - Otherwise, use a combination of tag and data-attributes (e.g., "button[data-automation-id='submit-application']").
   - As a last resort, use the text content (e.g., "button:has-text('Submit')").

RESPONSE FORMAT:
Return a JSON object with the following structure:
{{
    "found": true/false,
    "confidence": 0.0-1.0,
    "element_index": number (index of the best matching element),
    "text": "button text content",
    "selector": "CSS selector to target this element",
    "reason": "explanation of why this element was selected",
    "attributes": {{
        "key": "value"
    }}
}}

If no suitable button is found, set "found" to false and provide a brief reason.

IMPORTANT: Only return valid JSON, no other text.
"""
        
        return prompt
    
    def _parse_button_response(self, response_text: str, button_type: str) -> Optional[Dict[str, Any]]:
        """Parse Gemini's response to extract button information."""
        try:
            # Extract JSON from response
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if json_match:
                json_text = json_match.group(1)
            else:
                # Try to find JSON object in the response
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    json_text = json_match.group()
                else:
                    json_text = response_text.strip()
            
            result = json.loads(json_text)
            
            # Validate the result
            if not isinstance(result, dict):
                return None
            
            if not result.get('found', False):
                return result
            
            # Ensure required fields are present
            required_fields = ['confidence', 'element_index', 'text', 'selector', 'reason']
            for field in required_fields:
                if field not in result:
                    logger.warning(f"Missing required field '{field}' in AI response")
                    return None
            
            return result
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini button response as JSON: {e}")
            logger.debug(f"Response text: {response_text}")
            return None
        except Exception as e:
            logger.error(f"Error parsing button response: {e}")
            return None
    
    async def find_apply_button(self, page_text: str, context: str = "") -> Optional[Dict[str, Any]]:
        """Convenience method to find apply button."""
        return await self._find_button(page_text, 'apply', context)
    
    async def find_next_button(self, page_text: str, context: str = "") -> Optional[Dict[str, Any]]:
        """Convenience method to find next/continue button."""
        return await self._find_button(page_text, 'next', context)
    
    async def find_submit_button(self, page_text: str, context: str = "") -> Optional[Dict[str, Any]]:
        """Convenience method to find submit button."""
        return await self._find_button(page_text, 'submit', context)
