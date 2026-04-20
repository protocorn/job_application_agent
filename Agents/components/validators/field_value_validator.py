"""
Field value validator that cleans and formats values before filling.
Ensures phone numbers are properly formatted, URLs are complete, etc.
"""
import re
from typing import Any, Optional
from loguru import logger


class FieldValueValidator:
    """Validates and cleans field values before filling."""

    @staticmethod
    def clean_phone_number(phone: str, field_label: str = "") -> str:
        """
        Clean phone number to contain only digits and extension if needed.

        Examples:
            "(240) 610-1453" -> "2406101453"
            "+1 (240) 610-1453" -> "2406101453"
            "240-610-1453 ext 123" -> "2406101453" (unless field asks for extension)
        """
        if not phone:
            return phone

        # Check if field explicitly asks for extension
        asks_for_extension = any(keyword in field_label.lower()
                                for keyword in ['extension', 'ext', 'ext.'])

        # Remove country code prefixes like +1, +91, etc.
        phone = re.sub(r'^\+\d{1,3}\s*', '', phone)

        # If field asks for extension, extract it
        if asks_for_extension:
            ext_match = re.search(r'(?:ext\.?|extension)\s*(\d+)', phone, re.IGNORECASE)
            if ext_match:
                logger.debug(f"📞 Extracted extension: {ext_match.group(1)}")
                return ext_match.group(1)

        # Remove all non-digit characters (parentheses, spaces, dashes, etc.)
        cleaned = re.sub(r'\D', '', phone)

        # Remove extension part if present (after cleaning)
        # Typical US phone is 10 digits, anything longer might be extension
        if len(cleaned) > 10 and not asks_for_extension:
            cleaned = cleaned[:10]

        logger.debug(f"📞 Cleaned phone: '{phone}' -> '{cleaned}'")
        return cleaned

    # ── Known platform registry ───────────────────────────────────────────────
    # Each entry: (label_keywords, canonical_domain, www_prefix)
    # Detected from the *field label*, not the URL value — avoids sniffing
    # partial/malformed values and works for any platform uniformly.
    _PLATFORM_REGISTRY: list[tuple[tuple[str, ...], str, bool]] = [
        (('linkedin',),                          'linkedin.com',       True),
        (('github',),                            'github.com',         False),
        (('twitter', ' x ', 'x profile'),        'twitter.com',        False),
        (('instagram',),                         'instagram.com',      True),
        (('behance',),                           'behance.net',        True),
        (('dribbble',),                          'dribbble.com',       False),
        (('stackoverflow', 'stack overflow'),    'stackoverflow.com',  False),
        (('gitlab',),                            'gitlab.com',         False),
        (('medium',),                            'medium.com',         False),
        (('kaggle',),                            'kaggle.com',         True),
    ]

    @classmethod
    def _platform_from_label(cls, field_label: str) -> tuple[str, bool] | None:
        """
        Return (canonical_domain, use_www) if the field label implies a known
        platform, else None.
        """
        label_lower = field_label.lower()
        for keywords, domain, www in cls._PLATFORM_REGISTRY:
            if any(kw in label_lower for kw in keywords):
                return domain, www
        return None

    @staticmethod
    def _strip_to_path(url: str, domain: str) -> str:
        """
        Given a (possibly malformed) URL and the expected canonical domain,
        strip away any protocol, www, and any partial/full domain prefix so
        only the meaningful path remains.

        Examples:
            _strip_to_path('linkedin/in/john',       'linkedin.com') -> 'in/john'
            _strip_to_path('linkedin.com/in/john',   'linkedin.com') -> 'in/john'
            _strip_to_path('github/protocorn',       'github.com')   -> 'protocorn'
            _strip_to_path('https://github.com/foo', 'github.com')   -> 'foo'
            _strip_to_path('john',                   'linkedin.com') -> 'john'
        """
        # Strip protocol and www
        url = re.sub(r'^https?://', '', url, flags=re.IGNORECASE)
        url = re.sub(r'^www\.', '', url, flags=re.IGNORECASE)

        # Strip full canonical domain prefix  (e.g. "linkedin.com/")
        url = re.sub(rf'^{re.escape(domain)}/?', '', url, flags=re.IGNORECASE)

        # Strip bare platform name prefix without TLD (e.g. "linkedin/", "github/")
        domain_base = domain.split('.')[0]
        url = re.sub(rf'^{re.escape(domain_base)}/', '', url, flags=re.IGNORECASE)

        return url.strip('/')

    @staticmethod
    def is_valid_url(url: str) -> bool:
        """Check that a URL has a protocol and a domain containing a dot (TLD)."""
        if not url:
            return False
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            netloc = parsed.netloc.split(':')[0]  # strip port
            return bool(parsed.scheme) and '.' in netloc
        except Exception:
            return False

    @classmethod
    def format_url(cls, url: str, field_label: str = "") -> str:
        """
        Produce a valid, fully-qualified URL from a (possibly partial) value.

        Strategy:
          1. If the field label implies a known platform (LinkedIn, GitHub, …),
             extract the meaningful path from the value and build the canonical URL
             for that platform — regardless of what the stored value looks like.
          2. Otherwise apply generic formatting: add https:// and optionally www.
          3. Always validate at the end; callers should reject empty returns.

        Examples (field label → stored value → result):
          "LinkedIn URL"  / "linkedin/in/john"        → https://www.linkedin.com/in/john
          "LinkedIn URL"  / "https://www.linkedin/in/john" → https://www.linkedin.com/in/john
          "GitHub"        / "github/protocorn"        → https://github.com/protocorn
          "GitHub"        / "protocorn"               → https://github.com/protocorn
          "Website"       / "mysite.dev"              → https://www.mysite.dev
          "Website"       / "notaurl"                 → https://notaurl  (no dot → invalid → rejected)
        """
        if not url or not isinstance(url, str):
            return url

        url = url.strip()
        if not url:
            return url

        # ── Known platform (label-driven) ─────────────────────────────────────
        platform = cls._platform_from_label(field_label)
        if platform:
            domain, use_www = platform
            path = cls._strip_to_path(url, domain)
            prefix = f"https://{'www.' if use_www else ''}{domain}"
            formatted = f"{prefix}/{path}" if path else prefix
            logger.debug(f"🔗 Platform URL ({domain}) -> '{formatted}'")
            return formatted

        # ── Generic ───────────────────────────────────────────────────────────
        # Already has a protocol — detect and fix doubled-domain malformations
        # e.g. "https://github.com/www.github.com/user" → "https://github.com/user"
        if url.startswith(('http://', 'https://')):
            for _, domain, _ in cls._PLATFORM_REGISTRY:
                # Detect pattern: domain.com/www.domain.com/... or domain.com/domain.com/...
                doubled = re.search(
                    rf'(https?://(?:www\.)?{re.escape(domain)}/)(?:www\.)?{re.escape(domain)}/',
                    url, re.IGNORECASE
                )
                if doubled:
                    url = url[:doubled.end(1)] + url[doubled.end():]
                    logger.debug(f"🔗 Fixed doubled domain -> '{url}'")
                    break
            logger.debug(f"🔗 URL (with protocol): {url}")
            return url

        # Add www. if it looks like a real domain (has a dot, no spaces)
        if not url.startswith('www.') and '.' in url and ' ' not in url:
            url = f"www.{url}"

        formatted = f"https://{url}"
        logger.debug(f"🔗 Generic URL -> '{formatted}'")
        return formatted

    @staticmethod
    def validate_and_clean(value: Any, field_label: str, field_category: str) -> Any:
        """
        Main validation and cleaning function.

        Args:
            value: The value to clean
            field_label: Label of the field
            field_category: Category of the field

        Returns:
            Cleaned/formatted value
        """
        if not value:
            return value

        # Convert to string for processing
        value_str = str(value).strip()
        field_label_lower = field_label.lower()

        # Phone number fields
        if any(keyword in field_label_lower for keyword in [
            'phone', 'mobile', 'telephone', 'cell', 'contact number'
        ]):
            return FieldValueValidator.clean_phone_number(value_str, field_label)

        # URL fields (LinkedIn, GitHub, portfolio, website)
        if any(keyword in field_label_lower for keyword in [
            'linkedin', 'github', 'portfolio', 'website', 'url', 'link'
        ]) or field_category in ['url', 'website']:
            formatted = FieldValueValidator.format_url(value_str, field_label)
            if not FieldValueValidator.is_valid_url(formatted):
                logger.warning(f"🔗 Skipping invalid URL '{value_str}' for field '{field_label}'")
                return ""
            return formatted

        # Email - basic validation (already should be correct from profile)
        if 'email' in field_label_lower:
            # Just ensure no whitespace
            return value_str.strip()

        # Default: return as-is
        return value
