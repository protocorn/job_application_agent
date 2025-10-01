from __future__ import annotations
import logging
from typing import Dict, Any, Optional, Callable, Awaitable
from playwright.async_api import Page # Use the specific Page type for clarity

logger = logging.getLogger(__name__)

class ApplicationState:
    """Represents and tracks the current state of the job application process."""
    
    def __init__(self, initial_url: str):
        self.context: Dict[str, Any] = {'initial_url': initial_url}
        # History now tracks the state name and the URL at that time
        self.history: list[dict] = []
        self.current_state_name: Optional[str] = None

    def update_context(self, updates: Dict[str, Any]):
        """Update the shared context available to all states."""
        self.context.update(updates)

    def record_transition(self, from_state: str, to_state: str, url: str):
        """Record a state transition in the history for debugging and loop detection."""
        self.history.append({
            'from': from_state,
            'to': to_state,
            'url': url
        })
        logger.debug(f"History: {from_state} -> {to_state} @ {url}")

class StateMachine:
    """A robust, deterministic state machine for orchestrating the job application flow."""
    

    TERMINAL_STATES = ['success', 'fail']

    def __init__(self, initial_state: str, page: Page):
        self.page = page
        self._states: Dict[str, Callable[[ApplicationState], Awaitable[Optional[str]]]] = {}
        self._current_state_name = initial_state
        self.app_state = ApplicationState(page.url)
        self.max_transitions = 25  # A generous limit to prevent runaways

    def add_state(self, name: str, handler: Callable[[ApplicationState], Awaitable[Optional[str]]]):
        """Register a state and its corresponding handler function."""
        self._states[name] = handler

    async def run(self):
        """
        Runs the state machine until it reaches a terminal state or a halt condition.
        Terminal states are defined in TERMINAL_STATES or when the next state is None.
        """
        transition_count = 0
        while self._current_state_name and self._current_state_name not in self.TERMINAL_STATES:
            if transition_count >= self.max_transitions:
                logger.critical("⚠️ State machine exceeded max transitions. Halting to prevent runaway process.")
                self._current_state_name = 'fail'
                break

            if self._is_stuck_in_loop():
                logger.critical("🔁 State machine is stuck in a loop without making progress. Halting.")
                self.app_state.context['human_intervention_reason'] = "Agent is stuck in a loop on the same page. Please review."
                self._current_state_name = 'human_intervention'
                break

            handler = self._states.get(self._current_state_name)
            if not handler:
                logger.error(f"❌ Unknown state '{self._current_state_name}'. Halting.")
                self._current_state_name = 'fail'
                break

            logger.info(f"🚀 Entering state: {self._current_state_name}")
            self.app_state.current_state_name = self._current_state_name
            
            try:
                previous_state_name = self._current_state_name
                next_state_name = await handler(self.app_state)
                
                self.app_state.record_transition(previous_state_name, next_state_name, self.page.url)
                logger.info(f"✅ State '{previous_state_name}' completed, transitioning to '{next_state_name}'.")
                self._current_state_name = next_state_name

            except Exception as e:
                logger.error(f"❌ Unhandled error in state '{self._current_state_name}': {e}", exc_info=True)
                self._current_state_name = 'fail'

            transition_count += 1
        
        # Handle the final terminal state
        if self._current_state_name in self.TERMINAL_STATES:
            final_handler = self._states.get(self._current_state_name)
            if final_handler:
                logger.info(f"🚀 Entering terminal state: {self._current_state_name}")
                await final_handler(self.app_state)

        logger.info(f"🏁 State machine finished. Final state: {self._current_state_name or 'success'}")
        return self.app_state

    def _is_stuck_in_loop(self) -> bool:
        """A more intelligent loop detector."""
        if len(self.app_state.history) < 4:
            return False # Not enough history to detect a loop

        # A simple but effective loop is A -> B -> A -> B on the same URL.
        last_four = self.app_state.history[-4:]
        
        is_loop = (
            last_four[0]['from'] == last_four[2]['from'] and
            last_four[1]['from'] == last_four[3]['from'] and
            last_four[0]['to'] == last_four[2]['to'] and
            last_four[1]['to'] == last_four[3]['to']
        )
        
        # Crucially, check if the URL has changed. If it has, we are making progress.
        urls_are_the_same = (last_four[0]['url'] == last_four[2]['url'] and last_four[1]['url'] == last_four[3]['url'])

        return is_loop and urls_are_the_same