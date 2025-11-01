"""
Gemini API Rate Limiter with Exponential Backoff
Handles 429 RESOURCE_EXHAUSTED errors and queues requests
"""

import time
import functools
from typing import Any, Callable
from google import genai
from google.api_core import exceptions


class GeminiRateLimiter:
    """
    Rate limiter for Gemini API calls with automatic retry and backoff.

    Free tier: 10 requests per minute
    Strategy: Wait 60 seconds when rate limit hit, then retry
    """

    def __init__(self, requests_per_minute: int = 10):
        self.requests_per_minute = requests_per_minute
        self.request_timestamps = []
        self.last_rate_limit_time = None

    def _clean_old_requests(self):
        """Remove request timestamps older than 1 minute."""
        current_time = time.time()
        self.request_timestamps = [
            ts for ts in self.request_timestamps
            if current_time - ts < 60
        ]

    def _wait_if_needed(self):
        """Wait if we're approaching rate limit."""
        self._clean_old_requests()

        # If we've hit rate limit recently, wait
        if self.last_rate_limit_time:
            time_since_limit = time.time() - self.last_rate_limit_time
            if time_since_limit < 60:
                wait_time = 60 - time_since_limit
                print(f"⏳ Rate limit cooldown: waiting {wait_time:.0f} seconds...")
                time.sleep(wait_time)
                self.last_rate_limit_time = None

        # If we're at capacity, wait until oldest request expires
        if len(self.request_timestamps) >= self.requests_per_minute:
            oldest_request = self.request_timestamps[0]
            time_since_oldest = time.time() - oldest_request
            wait_time = 60 - time_since_oldest + 1  # +1 for safety margin

            if wait_time > 0:
                print(f"⏳ Approaching rate limit: waiting {wait_time:.0f} seconds...")
                time.sleep(wait_time)
                self._clean_old_requests()

    def call_with_retry(
        self,
        func: Callable,
        *args,
        max_retries: int = 3,
        **kwargs
    ) -> Any:
        """
        Call Gemini API function with automatic retry on rate limit.

        Args:
            func: The API function to call (e.g., client.models.generate_content)
            *args, **kwargs: Arguments to pass to func
            max_retries: Maximum number of retry attempts

        Returns:
            API response

        Raises:
            Exception if all retries exhausted
        """
        for attempt in range(max_retries + 1):
            try:
                # Wait if needed before making request
                self._wait_if_needed()

                # Make the API call
                response = func(*args, **kwargs)

                # Record successful request
                self.request_timestamps.append(time.time())

                return response

            except Exception as e:
                # Check if it's a rate limit error
                error_str = str(e)
                is_rate_limit = (
                    '429' in error_str or
                    'RESOURCE_EXHAUSTED' in error_str or
                    'quota' in error_str.lower()
                )

                if is_rate_limit:
                    self.last_rate_limit_time = time.time()

                    if attempt < max_retries:
                        # Extract wait time from error if available
                        wait_time = 60  # Default
                        if 'retryDelay' in error_str:
                            try:
                                import re
                                match = re.search(r"'retryDelay':\s*'(\d+)s'", error_str)
                                if match:
                                    wait_time = int(match.group(1))
                            except:
                                pass

                        print(f"⚠️  Rate limit hit. Waiting {wait_time} seconds before retry {attempt + 1}/{max_retries}...")
                        time.sleep(wait_time)
                        continue
                    else:
                        print(f"❌ Rate limit exceeded. All {max_retries} retries exhausted.")
                        raise
                else:
                    # Non-rate-limit error, raise immediately
                    raise

        raise Exception("Max retries exceeded")


# Global rate limiter instance
_global_rate_limiter = None


def get_rate_limiter(requests_per_minute: int = 10) -> GeminiRateLimiter:
    """Get or create global rate limiter instance."""
    global _global_rate_limiter
    if _global_rate_limiter is None:
        _global_rate_limiter = GeminiRateLimiter(requests_per_minute)
    return _global_rate_limiter


def generate_content_with_retry(
    client: genai.Client,
    model: str,
    contents: Any,
    config: Any = None,
    max_retries: int = 3
) -> Any:
    """
    Wrapper for client.models.generate_content with automatic retry.

    Usage:
        from gemini_rate_limiter import generate_content_with_retry

        response = generate_content_with_retry(
            client=client,
            model='gemini-2.0-flash-exp',
            contents='Your prompt here'
        )
    """
    rate_limiter = get_rate_limiter()

    def api_call():
        if config:
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=config
            )
        else:
            return client.models.generate_content(
                model=model,
                contents=contents
            )

    return rate_limiter.call_with_retry(api_call, max_retries=max_retries)


def batch_requests_with_delay(
    requests: list,
    requests_per_minute: int = 10,
    show_progress: bool = True
) -> list:
    """
    Execute a batch of requests with automatic rate limiting.

    Args:
        requests: List of callables (lambda functions) to execute
        requests_per_minute: Rate limit
        show_progress: Show progress messages

    Returns:
        List of responses

    Example:
        requests = [
            lambda: generate_content_with_retry(client, model, prompt1),
            lambda: generate_content_with_retry(client, model, prompt2),
            lambda: generate_content_with_retry(client, model, prompt3),
        ]
        responses = batch_requests_with_delay(requests, requests_per_minute=10)
    """
    rate_limiter = get_rate_limiter(requests_per_minute)
    results = []

    for i, request_func in enumerate(requests, 1):
        if show_progress:
            print(f"📤 Request {i}/{len(requests)}...")

        result = rate_limiter.call_with_retry(request_func)
        results.append(result)

        if show_progress and i < len(requests):
            print(f"✓ Completed {i}/{len(requests)}")

    return results


# Decorator for automatic retry
def with_rate_limit_retry(max_retries: int = 3):
    """
    Decorator to add automatic retry logic to any function.

    Usage:
        @with_rate_limit_retry(max_retries=3)
        def my_api_call(client, prompt):
            return client.models.generate_content(...)
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            rate_limiter = get_rate_limiter()
            return rate_limiter.call_with_retry(
                lambda: func(*args, **kwargs),
                max_retries=max_retries
            )
        return wrapper
    return decorator
