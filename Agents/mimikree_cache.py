"""
Mimikree Response Caching Module
Caches Mimikree API responses based on job description hash to save API calls during testing.
"""

import os
import json
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any

CACHE_DIR = Path(__file__).parent.parent / "Cache" / "mimikree"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def get_job_description_hash(job_description: str) -> str:
    """Generate a consistent hash for a job description."""
    # Normalize whitespace and lowercase for consistent hashing
    normalized = " ".join(job_description.lower().split())
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


def get_cached_mimikree_data(job_description: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve cached Mimikree data for a job description.

    Args:
        job_description: The job description text

    Returns:
        Cached data dict or None if not cached
    """
    job_hash = get_job_description_hash(job_description)
    cache_file = CACHE_DIR / f"mimikree_{job_hash}.json"

    if cache_file.exists():
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cached_data = json.load(f)
            print(f"✅ Using cached Mimikree data for job hash: {job_hash}")
            print(f"   Cache file: {cache_file}")
            return cached_data
        except Exception as e:
            print(f"⚠️  Error reading cache: {e}")
            return None

    return None


def cache_mimikree_data(job_description: str, mimikree_data: Dict[str, Any]) -> str:
    """
    Cache Mimikree data for a job description.

    Args:
        job_description: The job description text
        mimikree_data: The data to cache (questions and responses)

    Returns:
        Path to the cache file
    """
    job_hash = get_job_description_hash(job_description)
    cache_file = CACHE_DIR / f"mimikree_{job_hash}.json"

    try:
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(mimikree_data, f, indent=2, ensure_ascii=False)
        print(f"💾 Cached Mimikree data to: {cache_file}")
        return str(cache_file)
    except Exception as e:
        print(f"⚠️  Error caching data: {e}")
        return ""


def clear_mimikree_cache():
    """Clear all cached Mimikree data."""
    if CACHE_DIR.exists():
        for cache_file in CACHE_DIR.glob("mimikree_*.json"):
            cache_file.unlink()
        print(f"🗑️  Cleared Mimikree cache")


def list_cached_jobs() -> list:
    """List all cached job descriptions."""
    if not CACHE_DIR.exists():
        return []

    cached_jobs = []
    for cache_file in CACHE_DIR.glob("mimikree_*.json"):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            job_hash = cache_file.stem.replace("mimikree_", "")
            cached_jobs.append({
                'hash': job_hash,
                'file': str(cache_file),
                'timestamp': cache_file.stat().st_mtime
            })
        except Exception:
            pass

    return sorted(cached_jobs, key=lambda x: x['timestamp'], reverse=True)
