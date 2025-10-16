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

    def record_transition(self, from_state: str, to_state: str, url: str, progress_made: bool = False):
        """Record a state transition in the history for debugging and loop detection.

        Args:
            from_state: The state we're transitioning from
            to_state: The state we're transitioning to
            url: The current page URL
            progress_made: Whether meaningful progress was made (e.g., clicked button, filled form, dismissed blocker)
        """
        self.history.append({
            'from': from_state,
            'to': to_state,
            'url': url,
            'progress': progress_made
        })
        logger.debug(f"History: {from_state} -> {to_state} @ {url} [progress: {progress_made}]")

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

                # Check if progress was made during this transition
                progress_made = self.app_state.context.pop('progress_made', False)

                self.app_state.record_transition(previous_state_name, next_state_name, self.page.url, progress_made)
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
        """
        Enhanced loop detector that catches:
        - Short loops (A→B→A→B)
        - Long loops (A→B→C→D→A→B→C→D)
        - Partial progress loops (filling same field repeatedly)
        - Silent failure loops (no net progress despite activity)
        """
        if len(self.app_state.history) < 6:
            return False  # Need at least 6 entries to detect meaningful loops

        # Strategy 1: Check for repeated state signatures
        # Signature = (from_state, to_state, URL, fields_filled_count)
        recent_history = self.app_state.history[-10:]  # Last 10 transitions

        state_signatures = []
        for entry in recent_history:
            signature = (
                entry['from'],
                entry['to'],
                entry['url'],
                entry.get('fields_filled', 0)  # Track actual progress
            )
            state_signatures.append(signature)

        # Count occurrences of each signature
        signature_counts = {}
        for sig in state_signatures:
            signature_counts[sig] = signature_counts.get(sig, 0) + 1

        # If any signature appears 3+ times, we're stuck
        for sig, count in signature_counts.items():
            if count >= 3:
                logger.critical(f"🔁 Loop detected: State signature repeated {count} times: {sig}")
                return True

        # Strategy 2: Check for URL + field_count stagnation
        # If we've been on the same URL with same field count for 4+ transitions, we're stuck
        if len(recent_history) >= 4:
            last_four = recent_history[-4:]
            urls = [entry['url'] for entry in last_four]
            field_counts = [entry.get('fields_filled', 0) for entry in last_four]

            # All same URL and same field count = stuck
            if len(set(urls)) == 1 and len(set(field_counts)) == 1:
                # Check if any real progress was made
                any_progress = any(entry.get('progress', False) for entry in last_four)
                if not any_progress:
                    logger.critical(f"🔁 Stagnation detected: Same URL and field count for 4 transitions without progress")
                    return True

        # Strategy 3: Classic A→B→A→B pattern detection (backward compatibility)
        if len(recent_history) >= 4:
            last_four = recent_history[-4:]

            is_ab_loop = (
                last_four[0]['from'] == last_four[2]['from'] and
                last_four[1]['from'] == last_four[3]['from'] and
                last_four[0]['to'] == last_four[2]['to'] and
                last_four[1]['to'] == last_four[3]['to']
            )

            urls_same = (
                last_four[0]['url'] == last_four[2]['url'] and
                last_four[1]['url'] == last_four[3]['url']
            )

            any_progress = any(entry.get('progress', False) for entry in last_four)

            if is_ab_loop and urls_same and not any_progress:
                logger.critical(f"🔁 Classic A→B→A→B loop detected without progress")
                return True

        return False