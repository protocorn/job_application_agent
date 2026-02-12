"""
Proxy Manager for Job Application Agent
Manages proxy rotation to avoid IP bans
"""

import logging
import os
import random
from typing import List, Optional, Dict, Any
from collections import deque
import time

logger = logging.getLogger(__name__)


class ProxyManager:
    """
    Manages proxy rotation for job scraping to avoid IP bans
    
    Supports:
    - Proxy rotation (round-robin or random)
    - Failed proxy tracking
    - Multiple proxy formats
    - Proxy health checking
    """
    
    def __init__(self, proxies: Optional[List[str]] = None, rotation_strategy: str = "round_robin"):
        """
        Initialize proxy manager
        
        Args:
            proxies: List of proxies in format:
                - 'host:port'
                - 'user:pass@host:port'
                - 'http://host:port'
                - 'http://user:pass@host:port'
            rotation_strategy: 'round_robin' or 'random'
        """
        self.proxies = proxies or []
        self.rotation_strategy = rotation_strategy
        self.failed_proxies = set()
        self.proxy_usage_count = {}
        self.last_rotation_time = time.time()
        
        # Parse and validate proxies
        self.proxies = self._parse_proxies(self.proxies)
        
        # Initialize rotation queue
        if self.rotation_strategy == "round_robin":
            self.proxy_queue = deque(self.proxies)
        
        logger.info(f"ProxyManager initialized with {len(self.proxies)} proxies (strategy: {rotation_strategy})")
    
    def _parse_proxies(self, proxy_list: List[str]) -> List[str]:
        """Parse and normalize proxy formats"""
        parsed = []
        for proxy in proxy_list:
            proxy = proxy.strip()
            if not proxy:
                continue
            
            # Already in correct format (user:pass@host:port or host:port)
            if '@' in proxy or ':' in proxy:
                # Remove http:// or https:// prefix if present
                proxy = proxy.replace('http://', '').replace('https://', '')
                parsed.append(proxy)
        
        return parsed
    
    def get_next_proxy(self) -> Optional[str]:
        """
        Get next proxy in rotation
        
        Returns:
            Proxy string or None if no proxies available
        """
        if not self.proxies:
            return None
        
        # Remove failed proxies from available pool
        available_proxies = [p for p in self.proxies if p not in self.failed_proxies]
        
        if not available_proxies:
            logger.warning("All proxies have failed. Resetting failed proxy list.")
            self.failed_proxies.clear()
            available_proxies = self.proxies
        
        if self.rotation_strategy == "round_robin":
            # Get from queue and rotate
            if not self.proxy_queue:
                self.proxy_queue = deque(available_proxies)
            
            proxy = self.proxy_queue[0]
            self.proxy_queue.rotate(-1)  # Move to end
            
        else:  # random
            proxy = random.choice(available_proxies)
        
        # Track usage
        self.proxy_usage_count[proxy] = self.proxy_usage_count.get(proxy, 0) + 1
        self.last_rotation_time = time.time()
        
        logger.debug(f"Using proxy: {self._mask_proxy(proxy)} (used {self.proxy_usage_count[proxy]} times)")
        
        return proxy
    
    def mark_proxy_failed(self, proxy: str):
        """Mark a proxy as failed (won't be used until reset)"""
        if proxy:
            self.failed_proxies.add(proxy)
            logger.warning(f"Proxy marked as failed: {self._mask_proxy(proxy)}")
    
    def reset_failed_proxies(self):
        """Reset failed proxies (try them again)"""
        logger.info(f"Resetting {len(self.failed_proxies)} failed proxies")
        self.failed_proxies.clear()
    
    def get_proxy_list(self) -> List[str]:
        """Get list of all active proxies (for JobSpy)"""
        return [p for p in self.proxies if p not in self.failed_proxies]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get proxy usage statistics"""
        return {
            "total_proxies": len(self.proxies),
            "failed_proxies": len(self.failed_proxies),
            "active_proxies": len(self.proxies) - len(self.failed_proxies),
            "usage_count": dict(self.proxy_usage_count),
            "strategy": self.rotation_strategy
        }
    
    def _mask_proxy(self, proxy: str) -> str:
        """Mask proxy credentials for logging"""
        if '@' in proxy:
            # Has credentials
            parts = proxy.split('@')
            creds = parts[0]
            host = parts[1]
            # Mask password
            if ':' in creds:
                user = creds.split(':')[0]
                return f"{user}:****@{host}"
        return proxy
    
    @classmethod
    def from_env(cls) -> 'ProxyManager':
        """
        Create ProxyManager from environment variables
        
        Looks for:
        - PROXY_LIST: comma-separated proxy list
        - PROXY_FILE: path to file with proxies (one per line)
        """
        proxies = []
        
        # Try PROXY_LIST env var
        proxy_list_str = os.getenv("PROXY_LIST", "")
        if proxy_list_str:
            proxies.extend([p.strip() for p in proxy_list_str.split(',')])
        
        # Try PROXY_FILE env var
        proxy_file = os.getenv("PROXY_FILE", "")
        if proxy_file and os.path.exists(proxy_file):
            try:
                with open(proxy_file, 'r') as f:
                    file_proxies = [line.strip() for line in f if line.strip()]
                    proxies.extend(file_proxies)
            except Exception as e:
                logger.error(f"Failed to load proxies from file: {e}")
        
        strategy = os.getenv("PROXY_ROTATION_STRATEGY", "round_robin")
        
        return cls(proxies, strategy)
    
    @classmethod
    def from_file(cls, filepath: str, rotation_strategy: str = "round_robin") -> 'ProxyManager':
        """Load proxies from a file (one proxy per line)"""
        proxies = []
        try:
            with open(filepath, 'r') as f:
                proxies = [line.strip() for line in f if line.strip() and not line.startswith('#')]
            logger.info(f"Loaded {len(proxies)} proxies from {filepath}")
        except Exception as e:
            logger.error(f"Failed to load proxies from {filepath}: {e}")
        
        return cls(proxies, rotation_strategy)


# Predefined free proxy lists (use with caution - often unreliable)
FREE_PROXY_SOURCES = {
    "info": "Free proxies are often slow and unreliable. Consider paid proxies for production use.",
    "sources": [
        "https://free-proxy-list.net/",
        "https://www.proxy-list.download/",
        "https://www.sslproxies.org/"
    ]
}


def create_proxy_manager(
    proxy_list: Optional[List[str]] = None,
    proxy_file: Optional[str] = None,
    use_env: bool = True,
    rotation_strategy: str = "round_robin"
) -> Optional[ProxyManager]:
    """
    Convenience function to create ProxyManager
    
    Priority:
    1. proxy_list (if provided)
    2. proxy_file (if provided)
    3. Environment variables (if use_env=True)
    4. None (no proxies)
    
    Args:
        proxy_list: List of proxy strings
        proxy_file: Path to file with proxies
        use_env: Check environment variables
        rotation_strategy: 'round_robin' or 'random'
    
    Returns:
        ProxyManager instance or None
    """
    proxies = []
    
    # Priority 1: Direct proxy list
    if proxy_list:
        proxies.extend(proxy_list)
    
    # Priority 2: Proxy file
    elif proxy_file and os.path.exists(proxy_file):
        return ProxyManager.from_file(proxy_file, rotation_strategy)
    
    # Priority 3: Environment variables
    elif use_env:
        manager = ProxyManager.from_env()
        if manager.proxies:
            return manager
    
    # Create manager with collected proxies
    if proxies:
        return ProxyManager(proxies, rotation_strategy)
    
    logger.info("No proxies configured - using direct connection")
    return None


if __name__ == "__main__":
    # Test the proxy manager
    logging.basicConfig(level=logging.INFO)
    
    print("Testing Proxy Manager...\n")
    
    # Test 1: Basic rotation
    test_proxies = [
        "proxy1.example.com:8080",
        "user:pass@proxy2.example.com:8080",
        "proxy3.example.com:3128"
    ]
    
    manager = ProxyManager(test_proxies, rotation_strategy="round_robin")
    
    print("Round-robin rotation (5 requests):")
    for i in range(5):
        proxy = manager.get_next_proxy()
        print(f"  Request {i+1}: {proxy}")
    
    # Test 2: Mark one as failed
    print("\nMarking proxy2 as failed...")
    manager.mark_proxy_failed("user:pass@proxy2.example.com:8080")
    
    print("Next 3 requests (should skip failed proxy):")
    for i in range(3):
        proxy = manager.get_next_proxy()
        print(f"  Request {i+1}: {proxy}")
    
    # Test 3: Stats
    print("\nProxy Statistics:")
    stats = manager.get_stats()
    print(f"  Total: {stats['total_proxies']}")
    print(f"  Active: {stats['active_proxies']}")
    print(f"  Failed: {stats['failed_proxies']}")
    
    # Test 4: Random strategy
    print("\n" + "="*60)
    manager2 = ProxyManager(test_proxies, rotation_strategy="random")
    print("Random rotation (5 requests):")
    for i in range(5):
        proxy = manager2.get_next_proxy()
        print(f"  Request {i+1}: {proxy}")
