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

    @staticmethod
    def format_url(url: str, field_label: str = "") -> str:
        """
        Ensure URL is properly formatted with protocol.

        Examples:
            "linkedin.com/in/john" -> "https://www.linkedin.com/in/john"
            "github.com/john" -> "https://github.com/john"
            "example.com" -> "https://www.example.com"
            "www.example.com" -> "https://www.example.com"
        """
        if not url or not isinstance(url, str):
            return url

        url = url.strip()

        # Already has protocol
        if url.startswith(('http://', 'https://')):
            logger.debug(f"🔗 URL already formatted: {url}")
            return url

        # Special handling for known platforms
        if 'linkedin' in url.lower():
            if not url.startswith('www.'):
                url = f"www.{url}" if not url.startswith('linkedin.com') else url
            formatted = f"https://{url}"
            logger.debug(f"🔗 Formatted LinkedIn URL: '{url}' -> '{formatted}'")
            return formatted

        if 'github' in url.lower():
            if not url.startswith('github.com'):
                url = f"github.com/{url}"
            formatted = f"https://{url}"
            logger.debug(f"🔗 Formatted GitHub URL: '{url}' -> '{formatted}'")
            return formatted

        # Generic URL formatting
        if not url.startswith('www.'):
            # Check if it looks like a domain (has a dot and no spaces)
            if '.' in url and ' ' not in url:
                url = f"www.{url}"

        formatted = f"https://{url}"
        logger.debug(f"🔗 Formatted URL: '{url}' -> '{formatted}'")
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
        value_str = str(value)
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
            return FieldValueValidator.format_url(value_str, field_label)

        # Email - basic validation (already should be correct from profile)
        if 'email' in field_label_lower:
            # Just ensure no whitespace
            return value_str.strip()

        # Default: return as-is
        return value
