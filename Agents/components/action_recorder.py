"""
Action Recording System for Job Application Agent

This module provides functionality to record and replay user actions during 
form filling, eliminating the need for complex browser state management.
"""

import json
import time
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from playwright.async_api import Page

logger = logging.getLogger(__name__)


@dataclass
class ActionStep:
    """Represents a single action taken by the agent"""
    type: str  # navigate, fill_field, click, select_option, upload_file, wait, etc.
    timestamp: float
    selector: Optional[str] = None
    value: Optional[str] = None
    url: Optional[str] = None
    element_text: Optional[str] = None
    field_label: Optional[str] = None
    field_type: Optional[str] = None
    success: bool = True
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ActionStep':
        """Create ActionStep from dictionary"""
        return cls(**data)


class ActionRecorder:
    """Records all actions performed by the job application agent"""
    
    def __init__(self):
        self.actions: List[ActionStep] = []
        self.current_url: Optional[str] = None
        self.session_id: Optional[str] = None
        
    def start_recording(self, session_id: str, initial_url: str):
        """Start recording actions for a session"""
        self.session_id = session_id
        self.current_url = initial_url
        self.actions = []
        logger.info(f"Started action recording for session {session_id}")
        
        # Record initial navigation
        self.record_navigation(initial_url)
    
    def record_navigation(self, url: str, success: bool = True, error: Optional[str] = None):
        """Record a navigation action"""
        action = ActionStep(
            type="navigate",
            timestamp=time.time(),
            url=url,
            success=success,
            error=error
        )
        self.actions.append(action)
        self.current_url = url
        logger.debug(f"Recorded navigation to: {url}")
    
    def record_field_fill(self, selector: str, value: str, field_label: str = "", 
                         field_type: str = "", success: bool = True, error: Optional[str] = None):
        """Record filling a form field"""
        action = ActionStep(
            type="fill_field",
            timestamp=time.time(),
            selector=selector,
            value=value,
            field_label=field_label,
            field_type=field_type,
            success=success,
            error=error
        )
        self.actions.append(action)
        logger.debug(f"Recorded field fill: {field_label} = {value}")
    
    def record_click(self, selector: str, element_text: str = "", success: bool = True, 
                    error: Optional[str] = None):
        """Record clicking an element"""
        action = ActionStep(
            type="click",
            timestamp=time.time(),
            selector=selector,
            element_text=element_text,
            success=success,
            error=error
        )
        self.actions.append(action)
        logger.debug(f"Recorded click: {element_text or selector}")
    
    def record_select_option(self, selector: str, value: str, field_label: str = "",
                           success: bool = True, error: Optional[str] = None,
                           element_type: str = "select", interaction_method: str = "select_option",
                           option_attributes: Dict[str, Any] = None):
        """Record selecting an option from dropdown with complete context"""
        metadata = {
            "element_type": element_type,  # select, div, custom dropdown, etc.
            "interaction_method": interaction_method,  # select_option, click, type_and_select
            "option_attributes": option_attributes or {},  # data-value, value, text content
            "field_context": {
                "is_greenhouse_dropdown": "greenhouse" in selector.lower() or "combobox" in (option_attributes or {}).get("role", ""),
                "is_multiselect": False,  # TODO: detect multiselect
                "has_search": False  # TODO: detect searchable dropdowns
            }
        }
        
        action = ActionStep(
            type="select_option",
            timestamp=time.time(),
            selector=selector,
            value=value,
            field_label=field_label,
            success=success,
            error=error,
            metadata=metadata
        )
        self.actions.append(action)
        logger.debug(f"Recorded select option: {field_label} = {value} via {interaction_method}")
    
    def record_file_upload(self, selector: str, file_path: str, success: bool = True, 
                          error: Optional[str] = None):
        """Record file upload action"""
        action = ActionStep(
            type="upload_file",
            timestamp=time.time(),
            selector=selector,
            value=file_path,
            success=success,
            error=error
        )
        self.actions.append(action)
        logger.debug(f"Recorded file upload: {file_path}")
    
    def record_wait(self, duration_ms: int, reason: str = ""):
        """Record a wait/timeout action"""
        action = ActionStep(
            type="wait",
            timestamp=time.time(),
            value=str(duration_ms),
            metadata={"reason": reason}
        )
        self.actions.append(action)
        logger.debug(f"Recorded wait: {duration_ms}ms - {reason}")
    
    def record_failure(self, action_type: str, error: str, selector: str = "", 
                      context: Dict[str, Any] = None):
        """Record a failed action that caused the agent to stop"""
        action = ActionStep(
            type=f"failed_{action_type}",
            timestamp=time.time(),
            selector=selector,
            success=False,
            error=error,
            metadata=context or {}
        )
        self.actions.append(action)
        logger.warning(f"Recorded failure: {action_type} - {error}")
    
    def record_form_state_snapshot(self, form_data: Dict[str, Any], page_url: str):
        """Record a snapshot of the complete form state for verification"""
        action = ActionStep(
            type="form_state_snapshot",
            timestamp=time.time(),
            url=page_url,
            success=True,
            metadata={
                "form_data": form_data,
                "snapshot_reason": "verification_checkpoint",
                "field_count": len(form_data)
            }
        )
        self.actions.append(action)
        logger.debug(f"Recorded form state snapshot with {len(form_data)} fields")

    def record_page_state(self, page_url: str, page_title: str, page_type: str = "unknown", metadata: Dict[str, Any] = None):
        """Record the current page state for replay context"""
        action = ActionStep(
            type="page_state",
            timestamp=time.time(),
            url=page_url,
            success=True,
            metadata={
                "page_title": page_title,
                "page_type": page_type,
                **(metadata or {})
            }
        )
        self.actions.append(action)
        logger.debug(f"Recorded page state: {page_type} - {page_title}")

    def record_iframe_switch(self, iframe_selector: str, iframe_url: str = "", success: bool = True, error: Optional[str] = None):
        """Record switching to an iframe context"""
        action = ActionStep(
            type="iframe_switch",
            timestamp=time.time(),
            selector=iframe_selector,
            url=iframe_url,
            success=success,
            error=error,
            metadata={"context_type": "iframe"}
        )
        self.actions.append(action)
        logger.debug(f"Recorded iframe switch: {iframe_selector}")
    
    def record_enhanced_field_interaction(self, field_info: Dict[str, Any], value: Any, 
                                        interaction_result: Dict[str, Any]):
        """Record field interaction with complete context"""
        # Clean field_info to remove non-serializable objects
        clean_field_info = {
            "stable_id": field_info.get("stable_id", ""),
            "label": field_info.get("label", ""),
            "field_category": field_info.get("field_category", "unknown"),
            "placeholder": field_info.get("placeholder", ""),
            "required": field_info.get("required", False)
        }
        
        # Clean attributes to only include serializable data
        attributes = field_info.get("attributes", {})
        clean_attributes = {}
        for key, attr_value in attributes.items():
            if isinstance(attr_value, (str, int, float, bool)):
                clean_attributes[key] = attr_value
            else:
                clean_attributes[key] = str(attr_value)
        
        action = ActionStep(
            type="enhanced_field_fill",
            timestamp=time.time(),
            selector=field_info.get("stable_id", ""),
            value=str(value),
            field_label=field_info.get("label", ""),
            field_type=field_info.get("field_category", "unknown"),
            success=interaction_result.get("success", False),
            error=str(interaction_result.get("error", "")) if interaction_result.get("error") else None,
            metadata={
                "field_info": clean_field_info,
                "interaction_method": interaction_result.get("method", "unknown"),
                "element_attributes": clean_attributes,
                "final_value": str(interaction_result.get("final_value", value)),
                "verification": interaction_result.get("verification", {})
            }
        )
        self.actions.append(action)
        logger.debug(f"Recorded enhanced field interaction: {field_info.get('label')} = {value}")
    
    def get_successful_actions(self) -> List[ActionStep]:
        """Get only the successful actions for replay"""
        return [action for action in self.actions if action.success]
    
    def get_last_successful_step(self) -> Optional[ActionStep]:
        """Get the last successful action before failure"""
        successful_actions = self.get_successful_actions()
        return successful_actions[-1] if successful_actions else None
    
    def get_failure_point(self) -> Optional[ActionStep]:
        """Get the action where the agent failed"""
        failed_actions = [action for action in self.actions if not action.success]
        return failed_actions[0] if failed_actions else None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert recorder state to dictionary for storage"""
        result = {
            "session_id": self.session_id,
            "current_url": self.current_url,
            "actions": [action.to_dict() for action in self.actions],
            "total_actions": len(self.actions),
            "successful_actions": len(self.get_successful_actions()),
            "last_recorded": time.time()
        }
        
        # Test JSON serialization to catch any issues early
        try:
            json.dumps(result)
        except Exception as e:
            logger.error(f"Action recorder data is not JSON serializable: {e}")
            # Try to clean problematic data
            clean_actions = []
            for action in self.actions:
                try:
                    action_dict = action.to_dict()
                    json.dumps(action_dict)  # Test serialization
                    clean_actions.append(action_dict)
                except Exception as action_error:
                    logger.warning(f"Skipping non-serializable action: {action_error}")
            
            result["actions"] = clean_actions
            result["total_actions"] = len(clean_actions)
        
        return result
    
    def from_dict(self, data: Dict[str, Any]):
        """Load recorder state from dictionary"""
        self.session_id = data.get("session_id")
        self.current_url = data.get("current_url")
        self.actions = [ActionStep.from_dict(action_data) for action_data in data.get("actions", [])]
        logger.info(f"Loaded {len(self.actions)} actions for session {self.session_id}")
    
    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of recorded actions"""
        successful = self.get_successful_actions()
        failure_point = self.get_failure_point()
        
        return {
            "total_actions": len(self.actions),
            "successful_actions": len(successful),
            "failed_actions": len(self.actions) - len(successful),
            "last_successful_action": successful[-1].to_dict() if successful else None,
            "failure_point": failure_point.to_dict() if failure_point else None,
            "session_id": self.session_id,
            "current_url": self.current_url
        }


class ActionReplay:
    """Replays recorded actions to restore form state"""
    
    def __init__(self, page: Page):
        self.page = page
        
    async def replay_actions(self, actions: List[ActionStep], stop_at_failure: bool = True, progress_callback=None) -> bool:
        """
        Replay a list of actions

        Args:
            actions: List of actions to replay
            stop_at_failure: Whether to stop at the first failed action
            progress_callback: Optional callback function to report progress

        Returns:
            True if all actions were replayed successfully
        """
        logger.info(f"Starting replay of {len(actions)} actions")

        try:
            for i, action in enumerate(actions):
                if not action.success and stop_at_failure:
                    logger.info(f"Stopping replay at failed action: {action.type}")
                    break

                # Report progress if callback provided
                if progress_callback:
                    progress_callback(i + 1, len(actions), action.type, action.field_label or action.url or "")

                success = await self._replay_single_action(action)
                if not success and stop_at_failure:
                    logger.warning(f"Failed to replay action {i}: {action.type}")
                    return False

                # Small delay between actions for stability
                await self.page.wait_for_timeout(100)

            logger.info("Action replay completed successfully")
            return True

        except Exception as e:
            logger.error(f"Error during action replay: {e}")
            return False
    
    async def _replay_single_action(self, action: ActionStep) -> bool:
        """Replay a single action"""
        try:
            if action.type == "navigate":
                await self.page.goto(action.url, wait_until="domcontentloaded", timeout=30000)
                await self.page.wait_for_timeout(1000)  # Let page settle
                
            elif action.type == "fill_field" or action.type == "enhanced_field_fill":
                # Handle both legacy and enhanced field fill actions
                css_selector = self._convert_to_css_selector(action.selector)
                element = self.page.locator(css_selector).first

                try:
                    await element.wait_for(state="visible", timeout=5000)
                except Exception:
                    # Element might be attached but not visible, try to interact anyway
                    logger.warning(f"Element not visible: {action.field_label}, attempting anyway")

                # For enhanced actions, use the metadata to determine interaction method
                if action.type == "enhanced_field_fill" and action.metadata:
                    interaction_method = action.metadata.get("interaction_method", "text_fill")
                    field_type = action.field_type or "text_input"

                    if interaction_method == "file_upload":
                        # Handle file uploads
                        try:
                            await element.set_input_files(action.value)
                            logger.debug(f"✓ File upload: {action.value}")
                        except Exception as e:
                            logger.warning(f"File upload failed for {action.field_label}: {e}")
                    elif interaction_method in ["check", "check_fallback"] and field_type in ["selection", "radio", "checkbox"]:
                        # Handle checkboxes and radio buttons
                        if action.value.lower() in ['true', 'yes', '1', 'on', 'checked']:
                            try:
                                await element.check()
                                logger.debug(f"✓ Checked: {action.field_label}")
                            except Exception:
                                # Fallback to click
                                await element.click()
                                logger.debug(f"✓ Clicked (fallback): {action.field_label}")
                        else:
                            await element.click()
                            logger.debug(f"✓ Clicked: {action.field_label}")
                    elif interaction_method in ["click"] and field_type in ["selection", "radio", "checkbox"]:
                        # Direct click for checkboxes/radios
                        await element.click()
                        logger.debug(f"✓ Clicked: {action.field_label}")
                    elif interaction_method == "dropdown_selection":
                        # Handle dropdown selections
                        await self._replay_dropdown_selection(css_selector, action)
                        logger.debug(f"✓ Dropdown selected: {action.value}")
                    elif interaction_method == "workday_multiselect":
                        # Workday multiselect fields need special handling
                        logger.warning(f"Workday multiselect replay not fully supported yet: {action.field_label}")
                        # TODO: Implement workday multiselect replay
                    else:
                        # Default to text fill
                        try:
                            await element.fill(action.value)
                            logger.debug(f"✓ Text filled: {action.field_label} = {action.value}")
                        except Exception:
                            # Fallback to typing
                            await element.clear()
                            await element.type(action.value)
                            logger.debug(f"✓ Text typed (fallback): {action.field_label} = {action.value}")
                else:
                    # Legacy action - determine type and fill appropriately
                    field_type = action.field_type or "text_input"
                    if field_type in ["checkbox", "radio"]:
                        if action.value.lower() in ['true', 'yes', '1', 'on', 'checked']:
                            await element.check()
                            logger.debug(f"✓ Checked (legacy): {action.field_label}")
                        else:
                            await element.click()
                            logger.debug(f"✓ Clicked (legacy): {action.field_label}")
                    else:
                        await element.fill(action.value)
                        logger.debug(f"✓ Legacy fill: {action.field_label} = {action.value}")
                
            elif action.type == "click":
                css_selector = self._convert_to_css_selector(action.selector)
                element = self.page.locator(css_selector).first
                await element.wait_for(state="visible", timeout=5000)
                await element.click()
                
            elif action.type == "select_option":
                css_selector = self._convert_to_css_selector(action.selector)
                await self._replay_dropdown_selection(css_selector, action)
                
            elif action.type == "upload_file":
                css_selector = self._convert_to_css_selector(action.selector)
                element = self.page.locator(css_selector).first
                await element.wait_for(state="visible", timeout=5000)
                await element.set_input_files(action.value)
                
            elif action.type == "wait":
                duration = int(action.value) if action.value else 1000
                await self.page.wait_for_timeout(duration)
            
            elif action.type == "form_state_snapshot":
                # Skip snapshots during replay - they're just for verification
                logger.debug(f"Skipping snapshot action: {action.type}")
                return True

            elif action.type == "page_state":
                # Skip page state markers - they're just for context
                logger.debug(f"Page state marker: {action.metadata.get('page_type', 'unknown')}")
                return True

            elif action.type == "iframe_switch":
                # Handle iframe context switch
                logger.info(f"Switching to iframe: {action.selector}")
                try:
                    iframe = self.page.frame_locator(action.selector)
                    # Note: We can't directly switch context in replay, but we record it for reference
                    logger.debug(f"✓ Iframe context recorded: {action.selector}")
                except Exception as e:
                    logger.warning(f"Failed to locate iframe during replay: {e}")
                return True

            else:
                logger.debug(f"Skipping unknown action type: {action.type}")
                return True  # Don't fail on unknown types, just skip them
            
            logger.debug(f"✓ Replayed: {action.type} - {action.field_label or action.selector}")
            return True
            
        except Exception as e:
            logger.warning(f"Failed to replay action {action.type}: {e}")
            return False
    
    async def _replay_dropdown_selection(self, css_selector: str, action: ActionStep):
        """Replay dropdown selection with multiple strategies"""
        try:
            element = self.page.locator(css_selector).first

            try:
                await element.wait_for(state="visible", timeout=5000)
            except Exception:
                logger.warning(f"Dropdown element not visible: {action.field_label}, attempting anyway")

            metadata = action.metadata or {}
            interaction_method = metadata.get("interaction_method", "select_option")
            option_value = action.value
            display_text = option_value  # Use value as display text by default

            # Check if we have option_attributes with better display text
            option_attributes = metadata.get("option_attributes", {})
            if option_attributes:
                display_text = option_attributes.get("text", option_value)

            field_context = metadata.get("field_context", {})
            is_greenhouse = field_context.get("is_greenhouse_dropdown", False)

            logger.debug(f"Replaying dropdown: {action.field_label}, method: {interaction_method}, value: {option_value}, greenhouse: {is_greenhouse}")

            if interaction_method in ["select_option", "dropdown_selection"]:
                # Try standard select element first
                try:
                    await element.select_option(option_value)
                    logger.debug(f"✓ Selected option by value: {option_value}")
                    return
                except Exception as e:
                    logger.debug(f"Select by value failed, trying by text: {e}")
                    try:
                        await element.select_option(label=display_text)
                        logger.debug(f"✓ Selected option by text: {display_text}")
                        return
                    except Exception as e2:
                        logger.debug(f"Select by text failed: {e2}")
                        # For greenhouse/custom dropdowns, fall through to click method

            # Try custom dropdown (click to open, then click option)
            # This works for Greenhouse, Workday, and other custom dropdowns
            try:
                # Scroll into view first
                try:
                    await element.scroll_into_view_if_needed()
                    await self.page.wait_for_timeout(300)
                except Exception:
                    pass

                # Click to open dropdown
                await element.click(force=True)
                await self.page.wait_for_timeout(800)  # Wait for dropdown to open

                option_found = False

                # Strategy 1: Greenhouse-specific option selectors
                greenhouse_selectors = [
                    f'[data-value="{option_value}"]',
                    f'li:has-text("{display_text}")',
                    f'div[role="option"]:has-text("{display_text}")',
                    f'li[data-value="{option_value}"]'
                ]

                for selector in greenhouse_selectors:
                    try:
                        option_locator = self.page.locator(selector).first
                        if await option_locator.is_visible(timeout=2000):
                            await option_locator.click()
                            option_found = True
                            logger.debug(f"✓ Clicked greenhouse option with selector: {selector}")
                            break
                    except Exception:
                        continue

                # Strategy 2: Try exact text match
                if not option_found:
                    try:
                        option_locator = self.page.locator(f'text="{display_text}"').first
                        if await option_locator.is_visible(timeout=2000):
                            await option_locator.click()
                            option_found = True
                            logger.debug(f"✓ Clicked option by exact text: {display_text}")
                    except Exception:
                        pass

                # Strategy 3: Try role="option" with text match
                if not option_found:
                    try:
                        dropdown_options = self.page.locator('[role="option"]')
                        count = await dropdown_options.count()
                        for i in range(min(count, 50)):  # Limit iterations
                            option = dropdown_options.nth(i)
                            try:
                                if await option.is_visible(timeout=500):
                                    text = await option.text_content()
                                    if text and (display_text.lower() in text.lower() or text.lower() in display_text.lower()):
                                        await option.click()
                                        option_found = True
                                        logger.debug(f"✓ Clicked option in dropdown list: {text}")
                                        break
                            except Exception:
                                continue
                    except Exception:
                        pass

                # Strategy 4: Try generic option classes
                if not option_found:
                    try:
                        generic_options = self.page.locator('.option, [class*="option"], li')
                        count = await generic_options.count()
                        for i in range(min(count, 50)):
                            option = generic_options.nth(i)
                            try:
                                if await option.is_visible(timeout=500):
                                    text = await option.text_content()
                                    if text and (display_text.lower() in text.lower() or text.lower() in display_text.lower()):
                                        await option.click()
                                        option_found = True
                                        logger.debug(f"✓ Clicked option by class: {text}")
                                        break
                            except Exception:
                                continue
                    except Exception:
                        pass

                if not option_found:
                    logger.warning(f"Could not find dropdown option: {display_text}")
                    # Try to close dropdown
                    try:
                        await self.page.keyboard.press("Escape")
                    except Exception:
                        pass

            except Exception as e:
                logger.warning(f"Click dropdown strategy failed: {e}")
                
        except Exception as e:
            logger.warning(f"Failed to replay dropdown selection: {e}")
            raise

    def _convert_to_css_selector(self, selector: str) -> str:
        """Convert stored selector format to valid CSS selector"""
        if not selector:
            return selector
            
        # Handle our custom formats
        if selector.startswith('id:'):
            element_id = selector[3:]  # Remove 'id:' prefix
            # Use attribute selector for complex IDs (safer than # selector)
            return f'[id="{element_id}"]'
        elif selector.startswith('name:'):
            name = selector[5:]  # Remove 'name:' prefix
            return f'[name="{name}"]'
        else:
            # Assume it's already a valid CSS selector
            return selector
