import os
import time
import uuid
import re
import json
import unicodedata
import requests
from google import genai
import dotenv
from typing import List, Tuple, Optional, Dict, Any
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Systematic tailoring imports
try:
    from systematic_tailoring_complete import (
        run_systematic_tailoring,
        recover_from_overflow_if_needed
    )
    from mimikree_cache import get_cached_mimikree_data, cache_mimikree_data
    SYSTEMATIC_TAILORING_AVAILABLE = True
    print("[INIT] Systematic tailoring modules loaded successfully")
except ImportError as e:
    print(f"[INIT] WARNING: Systematic tailoring not available: {e}")
    SYSTEMATIC_TAILORING_AVAILABLE = False

dotenv.load_dotenv()

# Mimikree API Configuration
MIMIKREE_BASE_URL = os.getenv("MIMIKREE_BASE_URL", "http://localhost:3000")

# --- Google API Setup ---
SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
]


def get_google_services(credentials=None):
    """Get Google Docs and Drive services.

    Args:
        credentials: Optional Google OAuth2 Credentials object. If not provided,
                    falls back to token.json file.

    Returns:
        Tuple of (docs_service, drive_service)
    """
    creds = credentials

    # If no credentials provided, use legacy token.json file
    if not creds:
        # The file token.json stores the user's access and refresh tokens, and is
        # created automatically when the authorization flow completes for the first
        # time.
        if os.path.exists("token.json"):
            creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    "../credentials.json", SCOPES
                )
                creds = flow.run_local_server(port=0)
            # Save the credentials for the next run
            with open("token.json", "w") as token:
                token.write(creds.to_json())

    try:
        docs_service = build("docs", "v1", credentials=creds)
        drive_service = build("drive", "v3", credentials=creds)
        return docs_service, drive_service
    except HttpError as err:
        print(err)
        return None, None

def get_doc_id_from_url(url):
    """Extracts the Google Doc ID from a URL."""
    match = re.search(r"/document/d/([a-zA-Z0-9-_]+)", url)
    if match:
        return match.group(1)
    return None

def copy_google_doc(drive_service, doc_id, new_title):
    """Creates a copy of the specified Google Doc."""
    try:
        copy_metadata = {'name': new_title}
        copied_file = drive_service.files().copy(fileId=doc_id, body=copy_metadata).execute()
        return copied_file.get('id')
    except HttpError as error:
        print(f"An error occurred while copying the document: {error}")
        return None

def read_google_doc_content(docs_service, document_id):
    """Reads and returns the text content of a Google Doc."""
    try:
        document = docs_service.documents().get(documentId=document_id).execute()
        content = document.get('body').get('content')
        return read_structural_elements(content)
    except HttpError as error:
        print(f"An error occurred while reading the document: {error}")
        return None

def read_structural_elements(elements):
    """Recursively reads text from Google Docs structural elements with styling info."""
    text = ''
    for value in elements:
        if 'paragraph' in value:
            para_elements = value.get('paragraph').get('elements')
            for elem in para_elements:
                if 'textRun' in elem:
                    text_run = elem.get('textRun', {})
                    content = text_run.get('content', '')
                    text_style = text_run.get('textStyle', {})
                    
                    # Check for bold and italic styling
                    is_bold = text_style.get('bold', False)
                    is_italic = text_style.get('italic', False)
                    
                    # Wrap content with appropriate tags
                    if is_bold and is_italic:
                        content = f'<b><i>{content}</i></b>'
                    elif is_bold:
                        content = f'<b>{content}</b>'
                    elif is_italic:
                        content = f'<i>{content}</i>'
                    
                    text += content
    return text

def read_structural_elements_plain(elements):
    """Reads plain text from Google Docs structural elements without styling tags."""
    text = ''
    for value in elements:
        if 'paragraph' in value:
            para_elements = value.get('paragraph').get('elements')
            for elem in para_elements:
                text += elem.get('textRun', {}).get('content', '')
    return text

def extract_document_structure(docs_service, document_id):
    """Extract line-by-line structure with formatting metadata, detecting natural line wraps.

    Returns a list of line metadata dictionaries containing:
    - text: the actual text content (may span multiple visual lines)
    - alignment: left, center, right, justified
    - bullet_level: nesting level (0 = no bullet, 1+ = nested)
    - indent_start: indentation in points
    - indent_first_line: first line indent in points
    - char_limit: estimated max characters before line wrap
    - line_number: sequential line number
    - visual_lines: number of lines this text spans on page
    """
    try:
        document = docs_service.documents().get(documentId=document_id).execute()
        content = document.get('body', {}).get('content', [])

        # Default page width (8.5" with 1" margins = 6.5" = 468 points)
        # Average character width ~7 points for typical fonts at 11pt
        default_page_width = 468
        avg_char_width = 7

        line_metadata = []
        line_number = 0

        for element in content:
            if 'paragraph' in element:
                paragraph = element['paragraph']
                para_style = paragraph.get('paragraphStyle', {})
                bullet = paragraph.get('bullet', None)

                # Get alignment (default: START/left)
                alignment_map = {
                    'START': 'left',
                    'CENTER': 'center',
                    'END': 'right',
                    'JUSTIFIED': 'justified'
                }
                alignment = alignment_map.get(para_style.get('alignment', 'START'), 'left')

                # Get indentation
                indent_start = para_style.get('indentStart', {}).get('magnitude', 0)
                indent_first_line = para_style.get('indentFirstLine', {}).get('magnitude', 0)

                # Determine bullet level
                bullet_level = 0
                if bullet:
                    # Bullet level is determined by nesting level
                    nesting_level = bullet.get('nestingLevel', 0)
                    bullet_level = nesting_level + 1
                    # Add extra indent for bullets (typically 36 points per level)
                    indent_start += bullet_level * 36

                # Extract text content from paragraph
                para_elements = paragraph.get('elements', [])
                line_text = ''
                for elem in para_elements:
                    if 'textRun' in elem:
                        line_text += elem.get('textRun', {}).get('content', '')

                # Remove trailing newlines for analysis
                line_text_stripped = line_text.rstrip('\n')

                # Skip empty lines (but keep them in structure)
                if not line_text_stripped:
                    continue

                # Calculate available width for FIRST line (with first line indent)
                first_line_width = default_page_width - indent_start - indent_first_line
                first_line_char_limit = max(0, int(first_line_width / avg_char_width))

                # Calculate available width for CONTINUATION lines (without first line indent)
                continuation_width = default_page_width - indent_start
                continuation_char_limit = max(0, int(continuation_width / avg_char_width))

                # Estimate how many visual lines this paragraph spans
                current_length = len(line_text_stripped)
                visual_lines = 1  # At least 1 line

                if current_length > first_line_char_limit:
                    # Text wraps beyond first line
                    remaining_chars = current_length - first_line_char_limit
                    additional_lines = (remaining_chars + continuation_char_limit - 1) // continuation_char_limit
                    visual_lines += additional_lines

                # For char_buffer calculation, use the last line's remaining space
                if visual_lines == 1:
                    char_buffer = max(0, first_line_char_limit - current_length)
                else:
                    # Calculate chars on the last line
                    chars_before_last_line = first_line_char_limit + (visual_lines - 2) * continuation_char_limit
                    chars_on_last_line = current_length - chars_before_last_line
                    char_buffer = max(0, continuation_char_limit - chars_on_last_line)

                line_metadata.append({
                    'text': line_text_stripped,
                    'alignment': alignment,
                    'bullet_level': bullet_level,
                    'indent_start': indent_start,
                    'indent_first_line': indent_first_line,
                    'char_limit_first_line': first_line_char_limit,
                    'char_limit_continuation': continuation_char_limit,
                    'current_length': current_length,
                    'char_buffer': char_buffer,
                    'visual_lines': visual_lines,
                    'line_number': line_number
                })

                line_number += 1

        return line_metadata

    except HttpError as error:
        print(f"Error extracting document structure: {error}")
        return []

def calculate_line_budget(line_metadata, max_additional_lines=0):
    """Calculate how many lines can be added to the resume.

    Args:
        line_metadata: List of line metadata from extract_document_structure
        max_additional_lines: Maximum number of lines that can be added (default 0 = strict page preservation)

    Returns:
        Dictionary with:
        - current_paragraphs: number of paragraphs
        - current_visual_lines: total visual lines (accounting for wrapping)
        - max_allowed_lines: maximum lines allowed
        - available_lines: how many more lines can be added
        - underutilized_lines: potential lines that could be freed by shortening
    """
    current_paragraphs = len(line_metadata)

    # Calculate TOTAL visual lines (accounting for text wrapping)
    current_visual_lines = sum(line.get('visual_lines', 1) for line in line_metadata)

    # Calculate potential lines that could be freed by shortening content
    underutilized_lines = sum(
        1 for line in line_metadata
        if line.get('char_buffer', 0) > 20  # More than 20 chars of buffer
    )

    return {
        'current_paragraphs': current_paragraphs,
        'current_visual_lines': current_visual_lines,
        'max_allowed_lines': current_visual_lines + max_additional_lines,
        'available_lines': max_additional_lines,
        'underutilized_lines': underutilized_lines
    }

def verify_document_length(docs_service, document_id, original_visual_lines, tolerance=0):
    """Verify that document hasn't exceeded original page length.
    
    Args:
        docs_service: Google Docs API service
        document_id: Document ID to check
        original_visual_lines: Original number of visual lines
        tolerance: Number of additional lines allowed (default 0 = strict)
        
    Returns:
        Dictionary with:
        - within_limit: True if document is within page limits
        - current_visual_lines: Current visual line count
        - overflow_lines: Number of lines over limit (0 if within limit)
    """
    try:
        # Extract current document structure
        current_metadata = extract_document_structure(docs_service, document_id)
        current_visual_lines = sum(line.get('visual_lines', 1) for line in current_metadata)
        
        max_allowed = original_visual_lines + tolerance
        within_limit = current_visual_lines <= max_allowed
        overflow_lines = max(0, current_visual_lines - max_allowed)
        
        return {
            'within_limit': within_limit,
            'current_visual_lines': current_visual_lines,
            'original_visual_lines': original_visual_lines,
            'max_allowed_lines': max_allowed,
            'overflow_lines': overflow_lines
        }
    except Exception as e:
        print(f"Warning: Could not verify document length: {e}")
        # In case of error, assume it's okay to be conservative
        return {
            'within_limit': True,
            'current_visual_lines': original_visual_lines,
            'original_visual_lines': original_visual_lines,
            'max_allowed_lines': original_visual_lines,
            'overflow_lines': 0
        }

def score_replacement(replacement, line_metadata):
    """Score a replacement by its impact vs risk.
    
    Higher scores = better replacements (high impact, low risk of overflow)
    
    Returns:
        Score (float): Higher is better. Returns -1 if replacement is risky.
    """
    original_text = replacement.get('original_text', '')
    updated_text = replacement.get('updated_text', '')
    
    # Calculate length change
    original_len = len(original_text)
    updated_len = len(updated_text)
    length_change = updated_len - original_len
    
    # Find matching metadata for char_buffer
    char_buffer = None
    for meta in line_metadata or []:
        normalized_meta = _normalize_for_match(meta['text'])
        normalized_original = _normalize_for_match(original_text)
        if normalized_original in normalized_meta or normalized_meta in normalized_original:
            char_buffer = meta.get('char_buffer', 0)
            break
    
    # If we can't find metadata, be very conservative
    if char_buffer is None:
        if length_change > 0:
            return -1  # Reject additions when we don't know the buffer
        char_buffer = 0
    
    # Risk assessment
    if length_change > char_buffer:
        return -1  # Exceeds buffer - reject
    
    # Calculate impact score
    # Factors:
    # 1. Length-neutral or reductions are best (higher score)
    # 2. Small additions with large buffer are safe
    # 3. Longer original text = higher impact (more visible change)
    
    if length_change <= 0:
        # Length-neutral or reduction - excellent
        impact_score = 100 - length_change  # Reductions get bonus
    else:
        # Addition - score based on safety margin
        buffer_usage = length_change / max(char_buffer, 1)
        if buffer_usage > 0.6:  # Using >60% of buffer
            return -1  # Too risky
        impact_score = 50 * (1 - buffer_usage)  # Less buffer usage = higher score
    
    # Bonus for longer original text (more visible)
    visibility_bonus = min(20, original_len / 5)
    
    # Bonus if it has quantified data (important to preserve)
    if _extracts_quantified_data(original_text):
        visibility_bonus += 10
    
    return impact_score + visibility_bonus

def extract_job_keywords(job_description):
    """Extract important keywords and themes from job description using AI.

    Returns:
        Dictionary with:
        - required_skills: must-have technical/functional skills
        - preferred_skills: nice-to-have skills
        - key_themes: main themes (e.g., "leadership", "collaboration")
        - prioritized_keywords: ranked list of keywords to emphasize
    """
    try:
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

        prompt = f"""Analyze this job description and extract the most important keywords and themes.
Focus on skills, qualifications, and attributes that the employer is seeking.

Your task:
1. Identify REQUIRED skills (must-have technical and functional skills)
2. Identify PREFERRED skills (nice-to-have skills mentioned)
3. Extract KEY THEMES (e.g., "leadership", "innovation", "collaboration", "data-driven")
4. Create a PRIORITIZED list of keywords that should appear in the resume (most important first)

Return response in this JSON format:
{{
    "required_skills": ["skill1", "skill2", ...],
    "preferred_skills": ["skill1", "skill2", ...],
    "key_themes": ["theme1", "theme2", ...],
    "prioritized_keywords": ["keyword1", "keyword2", ...]
}}

JOB DESCRIPTION:
{job_description}
"""

        # Make API call
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )

        text = response.candidates[0].content.parts[0].text.strip()
        if text.startswith('```'):
            text = text.replace('```json', '').replace('```', '').strip()

        return json.loads(text)
    except Exception as e:
        print(f"Warning: Keyword extraction failed ({e}), continuing without keywords")
        return {
            "required_skills": [],
            "preferred_skills": [],
            "key_themes": [],
            "prioritized_keywords": []
        }

def annotate_resume_with_metadata(resume_text, line_metadata):
    """Annotate resume text with formatting constraints for each line.

    Adds metadata tags like [alignment=center, bullet_level=0, char_buffer=10, visual_lines=2]
    after each line to guide the AI.
    """
    lines = resume_text.split('\n')
    annotated_lines = []

    for idx, line in enumerate(lines):
        line_stripped = line.strip()
        if not line_stripped:
            annotated_lines.append(line)
            continue

        # Find matching metadata (by text content)
        matching_meta = None
        for meta in line_metadata:
            if line_stripped in meta['text'] or meta['text'] in line_stripped:
                matching_meta = meta
                break

        if matching_meta:
            # Add annotation with formatting constraints including visual_lines
            visual_lines = matching_meta.get('visual_lines', 1)
            annotation = f" [alignment={matching_meta['alignment']}, bullet_level={matching_meta['bullet_level']}, char_buffer={matching_meta['char_buffer']}, visual_lines={visual_lines}]"
            annotated_lines.append(line + annotation)
        else:
            annotated_lines.append(line)

    return '\n'.join(annotated_lines)

def _normalize_for_match(text):
    """Normalize text to reduce unicode/whitespace variance for matching-only checks."""
    if text is None:
        return ''
    # Remove HTML-style tags first
    t = re.sub(r'<[bi]>', '', text)
    t = re.sub(r'</[bi]>', '', t)
    # Normalize unicode (NFKC), map NBSP to space, unify quotes/dashes, collapse whitespace
    t = unicodedata.normalize('NFKC', t)
    t = t.replace('\u00A0', ' ')
    t = t.replace('"', '"').replace('"', '"').replace(''', "'").replace(''', "'")
    t = t.replace('–', '-').replace('—', '-')
    t = re.sub(r"\s+", " ", t)
    return t.strip()

def _is_header_text(text):
    headers = {
        'PROFILE', 'SUMMARY', 'SKILLS', 'EDUCATION', 'WORK EXPERIENCE', 'EXPERIENCE',
        'PROJECTS', 'PUBLICATIONS', 'ACHIEVEMENTS', 'CERTIFICATIONS', 'CONTACT'
    }
    return text.strip().upper() in headers

def _is_low_content_change(original_text, updated_text):
    # Very short targets or nearly identical outputs are low-value
    if len(original_text.strip()) < 10:
        return True
    o = _normalize_for_match(original_text).lower()
    u = _normalize_for_match(updated_text).lower()
    if o == u:
        return True
    # If updated merely wraps original with <= 4 extra chars, consider low value
    if o in u and (len(u) - len(o)) <= 4:
        return True
    return False

def _contains_banned_phrases(text):
    banned_patterns = [
        r"\bshowcasing\b",
        r"\bdemonstrating\b",
        r"\bability to\b",
        r"\bproficient in modern development tools\b",
        r"\bresults[- ]driven\b",
        r"\bpassionate about\b"
    ]
    t = _normalize_for_match(text).lower()
    return any(re.search(p, t) for p in banned_patterns)

def _extracts_quantified_data(original_text):
    """Check if original text contains numbers, percentages, or metrics."""
    # Match numbers, percentages, dollar amounts, etc.
    quantified_patterns = [
        r'\d+%',  # Percentages: 40%, 99%
        r'\d+[kKmMbB]?',  # Numbers with optional K/M/B: 100, 5K, 2M
        r'\$\d+',  # Dollar amounts: $100K
        r'\d+x',  # Multipliers: 10x, 2x
        r'\d+\+',  # Plus notation: 50+
    ]
    return any(re.search(p, original_text) for p in quantified_patterns)

def _removes_quantified_data(original_text, updated_text):
    """Check if updated text removes quantified data that was in original."""
    # Extract numbers from both
    original_numbers = re.findall(r'\d+\.?\d*', original_text)
    updated_numbers = re.findall(r'\d+\.?\d*', updated_text)

    # If original had numbers but updated doesn't, that's bad
    if len(original_numbers) > 0 and len(updated_numbers) == 0:
        return True

    # If original had more numbers than updated, check if significant ones were removed
    if len(original_numbers) > len(updated_numbers):
        # Allow minor differences (like year changes), but flag major removals
        return True

    return False

def _validate_replacements(raw_replacements, line_metadata=None, safety_margin=0.6):
    """Validate replacements including character limit checks with safety margins.

    Args:
        raw_replacements: List of replacement dictionaries
        line_metadata: Optional line metadata for character limit validation
        safety_margin: Use only this fraction of available char_buffer (default 0.6 = 60%)
    """
    valid = []
    invalid = []

    # Create lookup for line metadata by text
    metadata_lookup = {}
    if line_metadata:
        for meta in line_metadata:
            # Store by normalized text for fuzzy matching
            normalized_text = _normalize_for_match(meta['text'])
            metadata_lookup[normalized_text] = meta

    for rep in raw_replacements:
        original_text = (rep.get('original_text') or '').strip()
        updated_text = (rep.get('updated_text') or '').strip()
        reason = None

        if not original_text or not updated_text:
            reason = 'missing_text'
        elif '\n' in original_text or '\n' in updated_text:
            reason = 'contains_newline'
        elif _is_header_text(original_text):
            reason = 'header_text'
        elif _contains_banned_phrases(updated_text):
            reason = 'banned_phrase'
        elif _is_low_content_change(original_text, updated_text):
            reason = 'low_content_change'
        # Removed: _removes_quantified_data check - trust Gemini's judgment
        # If Gemini removed metrics, it had a good reason (e.g., better phrasing)
        elif line_metadata:
            # Check character limit constraints with SAFETY MARGIN
            # Remove metadata annotations from original_text for comparison
            original_clean = re.sub(r'\s*\[alignment=.*?\]', '', original_text)
            normalized_original = _normalize_for_match(original_clean)

            # Find matching metadata
            matching_meta = None
            for norm_text, meta in metadata_lookup.items():
                if normalized_original in norm_text or norm_text in normalized_original:
                    matching_meta = meta
                    break

            if matching_meta:
                # Calculate character difference
                original_len = len(original_clean)
                updated_len = len(updated_text)
                char_diff = updated_len - original_len

                # Apply safety margin to buffer (use only 60% by default)
                safe_buffer = int(matching_meta['char_buffer'] * safety_margin)
                
                # Check if it exceeds the SAFE buffer
                if char_diff > safe_buffer:
                    reason = f'exceeds_char_limit (added {char_diff}, safe limit {safe_buffer}/{matching_meta["char_buffer"]})'
            elif char_diff > 0:
                # No metadata found and it's an addition - reject to be safe
                reason = 'no_metadata_found_for_addition'

        if reason:
            invalid.append({'original_text': original_text, 'reason': reason})
        else:
            valid.append({'original_text': original_text, 'updated_text': updated_text})

    return valid, invalid

def _parse_styled_text(text: str) -> Tuple[str, List[dict]]:
    """Parse HTML-style tags and return plain text + style ranges."""
    if not text:
        return '', []
    
    # Track style ranges: [{'start': int, 'end': int, 'bold': bool, 'italic': bool}]
    styles = []
    plain_text = ''
    pos = 0
    text_pos = 0
    
    # Stack to track nested tags
    tag_stack = []
    
    while pos < len(text):
        # Look for opening tags
        if text[pos:pos+3] == '<b>':
            tag_stack.append({'type': 'bold', 'start': text_pos})
            pos += 3
        elif text[pos:pos+3] == '<i>':
            tag_stack.append({'type': 'italic', 'start': text_pos})
            pos += 3
        elif text[pos:pos+4] == '</b>':
            # Close bold tag
            for i in range(len(tag_stack) - 1, -1, -1):
                if tag_stack[i]['type'] == 'bold':
                    tag = tag_stack.pop(i)
                    styles.append({
                        'start': tag['start'],
                        'end': text_pos,
                        'bold': True,
                        'italic': False
                    })
                    break
            pos += 4
        elif text[pos:pos+4] == '</i>':
            # Close italic tag
            for i in range(len(tag_stack) - 1, -1, -1):
                if tag_stack[i]['type'] == 'italic':
                    tag = tag_stack.pop(i)
                    styles.append({
                        'start': tag['start'],
                        'end': text_pos,
                        'bold': False,
                        'italic': True
                    })
                    break
            pos += 4
        else:
            # Regular character
            plain_text += text[pos]
            text_pos += 1
            pos += 1
    
    return plain_text, styles

def _merge_style_ranges(style_ranges: List[dict]) -> List[dict]:
    """Merge duplicate ranges with the same start/end to combine bold/italic flags."""
    if not style_ranges:
        return []
    merged: dict = {}
    for r in style_ranges:
        key = (r.get('start'), r.get('end'))
        if key not in merged:
            merged[key] = {
                'start': r.get('start'),
                'end': r.get('end'),
                'bold': bool(r.get('bold')),
                'italic': bool(r.get('italic')),
            }
        else:
            merged[key]['bold'] = merged[key]['bold'] or bool(r.get('bold'))
            merged[key]['italic'] = merged[key]['italic'] or bool(r.get('italic'))
    # Return sorted by start then end for stable token insertion
    return sorted(merged.values(), key=lambda x: (x['start'], x['end']))



def _apply_styles_from_tags(docs_service, document_id, strip_tags: bool):
    """Apply styles using literal <b>/<i> tags present in the Doc.

    If strip_tags is True, remove the tags after styling; otherwise keep them.
    """
    # Read current plain text of document
    document = docs_service.documents().get(documentId=document_id).execute()
    content = document.get('body').get('content')
    doc_text = read_structural_elements_plain(content) or ''

    def find_tag_ranges(text: str, open_tag: str, close_tag: str) -> List[Tuple[int, int]]:
        ranges: List[Tuple[int, int]] = []
        stack: List[int] = []
        pos = 0
        open_len = len(open_tag)
        close_len = len(close_tag)
        while True:
            next_open = text.find(open_tag, pos)
            next_close = text.find(close_tag, pos)
            if next_open == -1 and next_close == -1:
                break
            if next_open != -1 and (next_close == -1 or next_open < next_close):
                # Push start of content after open tag
                stack.append(next_open + open_len)
                pos = next_open + open_len
            else:
                if stack:
                    start_content = stack.pop()
                    end_content = next_close
                    if end_content > start_content:
                        ranges.append((start_content, end_content))
                pos = next_close + close_len
        return ranges

    # Extract text snippets that should be bold/italic with their CLEAN versions
    # (without tags, as they will appear after tag stripping)
    bold_texts = []
    italic_texts = []

    for start, end in find_tag_ranges(doc_text, '<b>', '</b>'):
        if end > start:
            text_with_tags = doc_text[start:end]
            # Remove nested tags from the text we're searching for
            clean_text = text_with_tags.replace('<b>', '').replace('</b>', '').replace('<i>', '').replace('</i>', '')
            if clean_text.strip():
                bold_texts.append(clean_text)

    for start, end in find_tag_ranges(doc_text, '<i>', '</i>'):
        if end > start:
            text_with_tags = doc_text[start:end]
            clean_text = text_with_tags.replace('<b>', '').replace('</b>', '').replace('<i>', '').replace('</i>', '')
            if clean_text.strip():
                italic_texts.append(clean_text)

    print(f"Found {len(bold_texts)} text snippets to bold and {len(italic_texts)} to italicize")

    # Debug: print ALL bold texts we're looking for
    print("\n📋 All text to be bolded:")
    for idx, text in enumerate(bold_texts):
        print(f"  {idx+1}. '{text}'")

    # Save debug file showing what text has bold tags
    debug_bold_file = f"../Resumes/bold_debug_{uuid.uuid4().hex[:8]}.txt"
    with open(debug_bold_file, 'w', encoding='utf-8') as f:
        f.write("=== DOCUMENT WITH BOLD MARKERS ===\n\n")
        f.write("This shows what Gemini returned with <b> tags:\n\n")
        f.write(doc_text)
        f.write("\n\n=== LIST OF TEXT TO BE BOLDED ===\n\n")
        for idx, text in enumerate(bold_texts):
            f.write(f"{idx+1}. '{text}'\n")
    print(f"📄 Bold debug file saved to: {debug_bold_file}")

    # First, strip all tags from the document
    cleanup_requests = []
    for tok in ['<b>', '</b>', '<i>', '</i>']:
        cleanup_requests.append({
            'replaceAllText': {
                'containsText': {
                    'text': tok,
                    'matchCase': True
                },
                'replaceText': ''
            }
        })

    if cleanup_requests:
        docs_service.documents().batchUpdate(
            documentId=document_id,
            body={'requests': cleanup_requests}
        ).execute()
        print("Stripped tags from document")
        time.sleep(1)  # Wait for API to settle

    # Now apply formatting by finding the text snippets
    # Re-read the document after tag removal
    document = docs_service.documents().get(documentId=document_id).execute()
    content = document.get('body').get('content')
    clean_doc_text = read_structural_elements_plain(content) or ''

    format_requests = []

    # Apply bold formatting by finding each text snippet
    print("\n🎨 Applying bold formatting:")
    for idx, text_to_bold in enumerate(bold_texts):
        text_position = clean_doc_text.find(text_to_bold)
        if text_position == -1:
            print(f"❌ {idx+1}. Could not find: '{text_to_bold}'")
            print(f"   Searching in: ...{clean_doc_text[max(0, text_position-50):text_position+100]}...")
            continue

        # Google Docs uses 1-based indexing
        # endIndex is EXCLUSIVE, so we need +2 (one for 1-based, one for exclusive end)
        start_idx = text_position + 1
        end_idx = text_position + len(text_to_bold) + 2

        # Show context
        context_start = max(0, text_position - 20)
        context_end = min(len(clean_doc_text), text_position + len(text_to_bold) + 20)
        context = clean_doc_text[context_start:context_end]

        print(f"✓ {idx+1}. '{text_to_bold}' at position {text_position}")
        print(f"   Context: ...{context}...")
        print(f"   Range: [{start_idx}, {end_idx})")

        format_requests.append({
            'updateTextStyle': {
                'range': {
                    'startIndex': start_idx,
                    'endIndex': end_idx
                },
                'textStyle': {
                    'bold': True
                },
                'fields': 'bold'
            }
        })

    # Apply italic formatting
    for idx, text_to_italicize in enumerate(italic_texts):
        text_position = clean_doc_text.find(text_to_italicize)
        if text_position == -1:
            print(f"Warning: Could not find text to italicize: '{text_to_italicize[:30]}...'")
            continue

        # Same as bold - 1-based indexing with exclusive endIndex
        start_idx = text_position + 1
        end_idx = text_position + len(text_to_italicize) + 2

        print(f"Italicizing: '{text_to_italicize[:50]}...'")
        print(f"  Text position in doc: {text_position}, length: {len(text_to_italicize)}")
        print(f"  Applied range: [{start_idx}, {end_idx}) (endIndex is exclusive)")

        format_requests.append({
            'updateTextStyle': {
                'range': {
                    'startIndex': start_idx,
                    'endIndex': end_idx
                },
                'textStyle': {
                    'italic': True
                },
                'fields': 'italic'
            }
        })

    # Apply formatting in batches
    batch_size = 10
    for i in range(0, len(format_requests), batch_size):
        batch = format_requests[i:i + batch_size]
        if not batch:
            continue
        docs_service.documents().batchUpdate(
            documentId=document_id,
            body={'requests': batch}
        ).execute()
        print(f"Applied formatting batch {i//batch_size + 1}")

    print(f"✓ Bold/italic formatting applied successfully!")

    # Create a visual debug file showing what SHOULD be bold
    debug_visual_file = f"../Resumes/bold_visual_{uuid.uuid4().hex[:8]}.txt"
    with open(debug_visual_file, 'w', encoding='utf-8') as f:
        f.write("=== VISUAL REPRESENTATION OF WHAT SHOULD BE BOLD ===\n\n")
        f.write("Legend: **text** = should be bold\n\n")

        # Create a marked-up version of the clean text
        visual_text = clean_doc_text

        # Sort bold_texts by position (longest first to avoid substring issues)
        bold_positions = []
        for text in bold_texts:
            pos = clean_doc_text.find(text)
            if pos != -1:
                bold_positions.append((pos, len(text), text))

        bold_positions.sort(reverse=True)  # Start from end to not mess up positions

        # Insert markers
        for pos, length, text in bold_positions:
            visual_text = visual_text[:pos] + '**' + text + '**' + visual_text[pos+length:]

        f.write(visual_text)

        f.write("\n\n=== SUMMARY ===\n")
        f.write(f"Total bold requests: {len(bold_texts)}\n")
        f.write(f"Successfully found: {len([t for t in bold_texts if clean_doc_text.find(t) != -1])}\n")
        f.write(f"Not found: {len([t for t in bold_texts if clean_doc_text.find(t) == -1])}\n")

        not_found = [t for t in bold_texts if clean_doc_text.find(t) == -1]
        if not_found:
            f.write("\nText that couldn't be found:\n")
            for text in not_found:
                f.write(f"  - '{text}'\n")

    print(f"📄 Visual bold debug saved to: {debug_visual_file}")

    # IMPORTANT: Read the actual document after formatting to see what's REALLY bold
    print("\n🔍 Reading actual document to verify bold formatting...")
    time.sleep(2)  # Wait for formatting to settle

    final_document = docs_service.documents().get(documentId=document_id).execute()
    final_content = final_document.get('body', {}).get('content', [])

    # Create a debug file showing what's ACTUALLY bold in the document
    actual_bold_file = f"../Resumes/actual_bold_{uuid.uuid4().hex[:8]}.txt"
    with open(actual_bold_file, 'w', encoding='utf-8') as f:
        f.write("=== ACTUAL BOLD TEXT IN GOOGLE DOC ===\n\n")
        f.write("This shows what's actually bold in the final document:\n\n")

        for element in final_content:
            if 'paragraph' in element:
                para = element['paragraph']
                para_elements = para.get('elements', [])

                for elem in para_elements:
                    if 'textRun' in elem:
                        text_run = elem['textRun']
                        content = text_run.get('content', '')
                        text_style = text_run.get('textStyle', {})
                        is_bold = text_style.get('bold', False)

                        if is_bold:
                            f.write(f"**{content}**")
                        else:
                            f.write(content)

        f.write("\n\n=== SUMMARY ===\n")
        f.write("Text marked with **bold** is what's actually bold in the document.\n")
        f.write("Compare this with bold_visual_*.txt to see discrepancies.\n")

    print(f"📄 Actual bold formatting saved to: {actual_bold_file}")


def _regenerate_invalid_replacements(client, resume_text, job_description, invalid_items):
    if not invalid_items:
        return []
    # Ask model to only regenerate updated_text for the specified original_text entries
    originals_list = "\n".join([f"- {item['original_text']}" for item in invalid_items])
    prompt = f"""
You previously proposed replacements for tailoring a resume, but some items violated constraints
(generic language, headers, newlines, removed quantified data, or low-value changes). 

For the list below, regenerate ONLY the updated_text values. Keep original_text exactly the same.

⚠️  CRITICAL - PRESERVE ALL QUANTIFIED DATA:
- If original has "95%", your updated_text MUST include "95%"
- If original has "team of 3", your updated_text MUST include "3" or "team of 3"
- If original has "1,500+", your updated_text MUST include "1,500+"
- If original has "70% and 30%", your updated_text MUST include BOTH "70%" AND "30%"
- ONLY change words AROUND the numbers, NEVER remove the numbers!

EXAMPLES:
✅ GOOD: "Led team of 3 to design RAG engine" → "Led team of 3 building RAG engine for document analysis"
✅ GOOD: "70% expanded query + 30% RAG" → "hybrid approach (70% query expansion + 30% RAG retrieval)"
❌ BAD: "Led team of 3" → "Led cross-functional team" (REMOVED the number 3!)
❌ BAD: "70% expanded + 30% RAG" → "hybrid retrieval pipeline" (REMOVED both percentages!)

Other Constraints:
- No generic phrases (e.g., demonstrating, showcasing, ability to)
- No newlines, bullets, or headers
- Make concrete, job-relevant improvements using the job description
- Preserve professional tone and be specific
- 1 replacement per original_text
- You can use <b>text</b> for bold and <i>text</i> for italic styling if needed

Return strict JSON with this shape:
{{"replacements": [{{"original_text": "...", "updated_text": "..."}}]}}

Items to fix:
{originals_list}

RESUME:
{resume_text}

JOB DESCRIPTION:
{job_description}
"""
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )
    text = response.candidates[0].content.parts[0].text.strip()
    if text.startswith('```'):
        text = text.replace('```json', '').replace('```', '').strip()
    try:
        data = json.loads(text)
        return data.get('replacements', [])
    except Exception:
        return []

def apply_json_replacements_to_doc(docs_service, document_id, replacements_json, resume_text=None, job_description_text=None, keep_tags_literal=None, line_metadata=None):
    """Applies text replacements from JSON to Google Doc while preserving all formatting.

    Args:
        docs_service: Google Docs API service
        document_id: Document ID to apply replacements to
        replacements_json: JSON string with replacements
        resume_text: Optional original resume text for regeneration
        job_description_text: Optional job description for regeneration
        keep_tags_literal: Optional flag to keep HTML tags visible
        line_metadata: Optional line metadata for character limit validation
    """
    try:
        # Parse the JSON response from Gemini
        try:
            # Extract JSON from markdown code blocks if present
            json_text = replacements_json.strip()
            if json_text.startswith('```json'):
                # Remove markdown code block formatting
                json_text = json_text.replace('```json', '').replace('```', '').strip()
            elif json_text.startswith('```'):
                # Handle generic code blocks
                json_text = json_text.replace('```', '').strip()

            replacements_data = json.loads(json_text)
            replacements = replacements_data.get('replacements', [])

            # Extract line tracking info if present
            lines_added = replacements_data.get('lines_added', 0)
            lines_freed = replacements_data.get('lines_freed', 0)
            net_lines = replacements_data.get('net_lines', 0)

            if net_lines > 0:
                print(f"📊 Line budget tracking: Added {lines_added}, Freed {lines_freed}, Net {net_lines}")

        except json.JSONDecodeError as e:
            print(f"Error parsing JSON response: {e}")
            print(f"Raw response: {replacements_json}")
            return

        if not replacements:
            print("No replacements needed - resume is already well-tailored!")
            return

        print(f"Found {len(replacements)} text replacements from model")

        # Validate and regenerate invalid items with SAFETY MARGINS
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        safety_margin = float(os.getenv('REPLACEMENT_SAFETY_MARGIN', '0.5'))  # Default 50% of buffer
        valid, invalid = _validate_replacements(replacements, line_metadata=line_metadata, safety_margin=safety_margin)
        
        print(f"🔍 Validation Results: {len(valid)} valid, {len(invalid)} invalid replacements (safety margin: {int(safety_margin*100)}%)")
        
        if invalid:
            print(f"⚠️  Invalid items detected:")
            for item in invalid:
                print(f"   - '{item['original_text'][:50]}...' (Reason: {item['reason']})")
            
            if resume_text is not None and job_description_text is not None:
                print(f"🔄 Attempting to regenerate {len(invalid)} invalid items...")
                regenerated = _regenerate_invalid_replacements(client, resume_text, job_description_text, invalid)
                regenerated_valid, regenerated_invalid = _validate_replacements(regenerated)
                valid.extend(regenerated_valid)
                print(f"✅ Regeneration complete: {len(regenerated_valid)} successful, {len(regenerated_invalid)} still invalid")
                
                if regenerated_invalid:
                    print(f"❌ Items that couldn't be regenerated:")
                    for item in regenerated_invalid:
                        print(f"   - '{item['original_text'][:50]}...' (Reason: {item['reason']})")
            else:
                print("⚠️  Cannot regenerate: missing resume_text or job_description_text")
        else:
            print("✅ All replacements passed validation - no regeneration needed")

        if not valid:
            print("No valid replacements after validation. Aborting.")
            return

        # Score and prioritize replacements (best first)
        print(f"\n📊 Scoring and prioritizing {len(valid)} valid replacements...")
        scored_replacements = []
        for rep in valid:
            score = score_replacement(rep, line_metadata)
            if score >= 0:  # Only include non-negative scores
                scored_replacements.append({
                    'replacement': rep,
                    'score': score
                })
        
        # Sort by score (highest first)
        scored_replacements.sort(key=lambda x: x['score'], reverse=True)
        
        # Limit number of replacements to reduce risk
        max_replacements = int(os.getenv('MAX_REPLACEMENTS_PER_ITERATION', '5'))
        if len(scored_replacements) > max_replacements:
            print(f"⚠️  Limiting to top {max_replacements} replacements (from {len(scored_replacements)}) to minimize overflow risk")
            scored_replacements = scored_replacements[:max_replacements]
        
        # Show prioritized list
        print(f"📋 Prioritized replacements (top {len(scored_replacements)}):")
        for i, item in enumerate(scored_replacements, 1):
            rep = item['replacement']
            score = item['score']
            orig_preview = rep['original_text'][:40]
            print(f"  {i}. Score {score:.1f}: '{orig_preview}...'")
        
        # Extract just the replacements
        valid = [item['replacement'] for item in scored_replacements]

        # Read doc plain text to count occurrences (use plain version for matching)
        document = docs_service.documents().get(documentId=document_id).execute()
        doc_text = read_structural_elements_plain(document.get('body').get('content')) or ''
        doc_text_norm = _normalize_for_match(doc_text)

        api_requests = []
        style_requests = []
        marker_specs_all = []

        # Determine whether to keep tags literally in the doc (debug inspection mode)
        if keep_tags_literal is None:
            keep_tags_literal = os.getenv('KEEP_TAGS_LITERAL', 'false').lower() in ('1', 'true', 'yes')
        
        for item in valid:
            original_text = item['original_text']
            updated_text = item['updated_text']
            
            # Parse styling from updated_text
            plain_updated_text, style_ranges = _parse_styled_text(updated_text)
            
            # Remove styling tags from original_text for matching against plain doc_text
            plain_original_text = _parse_styled_text(original_text)[0]
            
            # Ensure exact, unique occurrence; eliminate fuzzy matching
            count = doc_text.count(plain_original_text)
            if count == 1:
                if keep_tags_literal:
                    # Insert updated_text verbatim (with <b>/<i> visible) for inspection
                    api_requests.append({
                        'replaceAllText': {
                            'containsText': {
                                'text': plain_original_text,
                                'matchCase': True
                            },
                            'replaceText': updated_text
                        }
                    })
                    print(f"Queued literal-tag replace for: '{plain_original_text[:60]}...'")
                else:
                    # ALWAYS use plain text (no tags) in replaceAllText
                    api_requests.append({
                        'replaceAllText': {
                            'containsText': {
                                'text': plain_original_text,
                                'matchCase': True
                            },
                            'replaceText': plain_updated_text
                        }
                    })

                    # Store formatting info if there are style ranges
                    if style_ranges:
                        marker_specs_all.append({
                            'text': plain_updated_text,
                            'style_ranges': style_ranges
                        })
                        print(f"Queued plain replace for: '{plain_original_text[:60]}...' (with {len(style_ranges)} style ranges)")
                        print(f"  → Will format: '{plain_updated_text[:60]}...'")
                        print(f"  → Updated text (with tags): '{updated_text[:80]}...'")
                        print(f"  → Style ranges: {style_ranges}")
                    else:
                        print(f"Queued plain replace for: '{plain_original_text[:60]}...'")
            elif count == 0:
                # Attempt a normalized check to warn about unicode issues (no replacement applied)
                if _normalize_for_match(plain_original_text) in doc_text_norm:
                    print(f"Warning: normalized match found but exact text not present; skipping: '{plain_original_text[:60]}...'")
                else:
                    print(f"! Could not find match for: '{plain_original_text[:60]}...' — skipping")
            else:
                print(f"Warning: multiple ({count}) occurrences of target text; skipping to avoid over-replacement")

        print(f"Applying {len(api_requests)} replaceAllText operations")

        # Apply text replacements in batches (preserving existing formatting)
        batch_size = 10
        for i in range(0, len(api_requests), batch_size):
            batch = api_requests[i:i + batch_size]
            if batch:
                docs_service.documents().batchUpdate(
                    documentId=document_id,
                    body={'requests': batch}
                ).execute()
                print(f"Applied batch {i//batch_size + 1}")

        print("✓ Text replacements applied successfully")

        # Now apply formatting if we have marker specs
        if marker_specs_all:
            print(f"\n🎨 Applying bold/italic formatting using Google Docs API structure...")
            print(f"📋 Debug: marker_specs_all contains {len(marker_specs_all)} items:")
            for i, spec in enumerate(marker_specs_all):
                print(f"  {i+1}. Text: '{spec['text'][:60]}...'")
                print(f"      Style ranges: {spec['style_ranges']}")
            time.sleep(1)  # Wait for replacements to settle

            # Re-read the document to get the updated structure
            doc_after_replace = docs_service.documents().get(documentId=document_id).execute()
            print(f"🔍 Debug: doc_after_replace keys: {doc_after_replace.keys()}")
            body = doc_after_replace.get('body', {})
            print(f"🔍 Debug: body keys: {body.keys() if body else 'None'}")
            content_after = body.get('content', [])
            print(f"🔍 Debug: content_after type: {type(content_after)}, length: {len(content_after)}")

            format_requests = []

            # Build complete document text with index mapping
            # Text can span multiple textRuns, so we need to reconstruct the full text
            # We manually track the cumulative index since startIndex may not be present after replacements
            doc_text_parts = []
            cumulative_index = 1  # Google Docs uses 1-based indexing
            
            print(f"🔍 Debug: content_after has {len(content_after)} elements")
            for idx, element in enumerate(content_after):
                if 'paragraph' in element:
                    para = element['paragraph']
                    para_elements = para.get('elements', [])
                    if idx < 5:  # Only show first 5 for brevity
                        print(f"🔍 Debug: Element {idx} is paragraph with {len(para_elements)} elements")
                    for elem in para_elements:
                        if 'textRun' in elem:
                            text_run = elem['textRun']
                            content = text_run.get('content', '')
                            # Use provided startIndex if available, otherwise use our cumulative tracker
                            start_index = text_run.get('startIndex')
                            if start_index is None:
                                start_index = cumulative_index
                            
                            if idx < 5 and len(doc_text_parts) < 10:  # Only show first few
                                print(f"🔍 Debug: Found textRun with startIndex={start_index}, content[:30]='{content[:30]}'")
                            
                            doc_text_parts.append({
                                'content': content,
                                'start_index': start_index
                            })
                            
                            # Update cumulative index for next textRun
                            cumulative_index = start_index + len(content)
            
            print(f"🔍 Debug: Collected {len(doc_text_parts)} text parts")

            # Reconstruct full document text
            full_doc_text = ''.join([part['content'] for part in doc_text_parts])

            print(f"\n🔍 Debug: Full document text length: {len(full_doc_text)}")
            print(f"🔍 Debug: First 200 chars: '{full_doc_text[:200]}'")

            for spec in marker_specs_all:
                target_text = spec['text']
                style_ranges = spec['style_ranges']

                # Search for target text in full document
                text_position = full_doc_text.find(target_text)
                if text_position == -1:
                    print(f"⚠️  Could not find text to format: '{target_text[:50]}...'")
                    # Debug: show where we're searching
                    print(f"   🔍 Debug: Searching for text with length {len(target_text)}")
                    print(f"   🔍 Debug: First 100 chars of target: '{target_text[:100]}'")
                    # Try partial match
                    if target_text[:30] in full_doc_text:
                        print(f"   ✓ Found first 30 chars in document!")
                        pos = full_doc_text.find(target_text[:30])
                        print(f"   Context: ...{full_doc_text[max(0,pos-20):pos+80]}...")
                    else:
                        print(f"   ✗ Not even first 30 chars found in document")
                    continue

                # Find the Google Docs startIndex for this position
                # Walk through doc_text_parts to find which textRun contains this position
                cumulative_length = 0
                found_start_index = None

                for part in doc_text_parts:
                    part_length = len(part['content'])
                    if cumulative_length <= text_position < cumulative_length + part_length:
                        # Found the textRun containing the start of our text
                        offset_in_run = text_position - cumulative_length
                        found_start_index = part['start_index'] + offset_in_run
                        break
                    cumulative_length += part_length

                if found_start_index is None:
                    print(f"⚠️  Could not determine document index for: '{target_text[:50]}...'")
                    continue

                # Apply each style range within this text
                for style_range in style_ranges:
                    range_start = found_start_index + style_range['start']
                    range_end = found_start_index + style_range['end']
                    
                    # Extract the text to be formatted
                    text_to_format = target_text[style_range['start']:style_range['end']]

                    if style_range['bold']:
                        # Check if text starts with numeric data (digits, percentages, etc.)
                        # Google Docs API has issues recognizing digits, so we shift the start index
                        # Example: "95%" → shift by 3, "~80%" → shift by 4, "1500+" → shift by 5
                        numeric_match = re.match(r'^[~]?\d+[+%kKmMbB]?\s*', text_to_format)
                        
                        if numeric_match:
                            # Shift start index by the length of numeric portion
                            numeric_length = len(numeric_match.group(0))
                            # Bold the entire text INCLUDING numbers (numbers should be bold too!)
                            format_requests.append({
                                'updateTextStyle': {
                                    'range': {
                                        'startIndex': range_start,
                                        'endIndex': range_end
                                    },
                                    'textStyle': {'bold': True},
                                    'fields': 'bold'
                                }
                            })
                            print(f"  Bold (including numbers): '{text_to_format}' at [{range_start}, {range_end})")
                        else:
                            # No numeric prefix, apply bold normally
                            format_requests.append({
                                'updateTextStyle': {
                                    'range': {
                                        'startIndex': range_start,
                                        'endIndex': range_end
                                    },
                                    'textStyle': {'bold': True},
                                    'fields': 'bold'
                                }
                            })
                            print(f"  Bold: '{text_to_format}' at [{range_start}, {range_end})")

                    if style_range['italic']:
                        # Same workaround for italic with numeric prefixes
                        numeric_match = re.match(r'^[~]?\d+[+%kKmMbB]?\s*', text_to_format)
                        
                        if numeric_match:
                            # Shift start index by the length of numeric portion
                            numeric_length = len(numeric_match.group(0))
                            adjusted_range_start = range_start + numeric_length
                            
                            # Italicize the entire text INCLUDING numbers
                            format_requests.append({
                                'updateTextStyle': {
                                    'range': {
                                        'startIndex': range_start,
                                        'endIndex': range_end
                                    },
                                    'textStyle': {'italic': True},
                                    'fields': 'italic'
                                }
                            })
                            print(f"  Italic (including numbers): '{text_to_format}' at [{range_start}, {range_end})")
                        else:
                            format_requests.append({
                                'updateTextStyle': {
                                    'range': {
                                        'startIndex': range_start,
                                        'endIndex': range_end
                                    },
                                    'textStyle': {'italic': True},
                                    'fields': 'italic'
                                }
                            })
                            print(f"  Italic: '{text_to_format}' at [{range_start}, {range_end})")

            # Apply all formatting requests
            if format_requests:
                batch_size = 10
                for i in range(0, len(format_requests), batch_size):
                    batch = format_requests[i:i + batch_size]
                    docs_service.documents().batchUpdate(
                        documentId=document_id,
                        body={'requests': batch}
                    ).execute()
                print(f"✅ Applied {len(format_requests)} formatting operations")
            else:
                print("No formatting to apply")
        else:
            print("No formatting specified")
        
    except HttpError as error:
        print(f"An error occurred while applying changes: {error}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        import traceback
        traceback.print_exc()

def get_actual_page_count(drive_service, document_id):
    """Get ACTUAL page count by exporting to PDF and checking PDF pages.
    
    This is the ONLY reliable way to get true page count with formatting considered.
    
    Args:
        drive_service: Google Drive API service
        document_id: Document ID to check
        
    Returns:
        int: Actual number of pages in the document
    """
    try:
        import io
        from PyPDF2 import PdfReader
        
        # Export document as PDF
        request = drive_service.files().export_media(
            fileId=document_id,
            mimeType='application/pdf'
        )
        
        # Download PDF to memory
        pdf_bytes = io.BytesIO()
        pdf_bytes.write(request.execute())
        pdf_bytes.seek(0)
        
        # Read PDF and count pages
        pdf_reader = PdfReader(pdf_bytes)
        page_count = len(pdf_reader.pages)
        
        return page_count
    except ImportError:
        print("WARNING: PyPDF2 not installed. Run: pip install PyPDF2")
        return None
    except Exception as e:
        print(f"WARNING: Could not get PDF page count: {e}")
        return None

def download_doc_as_pdf(drive_service, doc_id, pdf_path):
    """Downloads a Google Doc as a PDF file."""
    try:
        request = drive_service.files().export_media(fileId=doc_id, mimeType='application/pdf')
        with open(pdf_path, 'wb') as f:
            f.write(request.execute())
        print(f"Successfully downloaded tailored resume to {pdf_path}")
    except HttpError as error:
        print(f"An error occurred during PDF download: {error}")

def tailor_resume(resume_text, job_description, line_metadata=None, line_budget=None, keywords=None):
    """Enhanced resume tailoring with format preservation and keyword optimization.

    Args:
        resume_text: Original resume text (possibly annotated with metadata)
        job_description: Job description text
        line_metadata: Optional line metadata from extract_document_structure
        line_budget: Optional line budget from calculate_line_budget
        keywords: Optional keywords from extract_job_keywords
    """
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    # Build format constraints section
    format_constraints = ""
    if line_budget:
        format_constraints = f"""
FORMAT PRESERVATION CONSTRAINTS (CRITICAL - MUST FOLLOW):
- Current resume: {line_budget['current_visual_lines']} visual lines (some paragraphs wrap to multiple lines)
- Available lines to add: {line_budget['available_lines']} (STRICT: DO NOT EXCEED)
- Each paragraph shows [char_buffer=X, visual_lines=Y]
  * char_buffer: MAX characters you can ADD before causing text to wrap to next line
  * visual_lines: How many lines this paragraph currently spans
- Example: "Led team [char_buffer=10, visual_lines=1]" → Can add up to 10 chars
- DO NOT remove or reduce quantified data (numbers, percentages, metrics)
  * WRONG: "Improved performance by 40%" → "Improved performance significantly"
  * RIGHT: "Improved performance by 40%" → "Optimized performance by 40%"
- PRESERVE original resume length - if it was 1 page, keep it 1 page
- Text that naturally wraps is ONE paragraph - don't treat wrapped lines as separate items
"""

    # Build keyword prioritization section
    keyword_section = ""
    if keywords:
        required_skills_str = ", ".join(keywords.get('required_skills', []))
        key_themes_str = ", ".join(keywords.get('key_themes', []))
        prioritized_str = ", ".join(keywords.get('prioritized_keywords', [])[:10])  # Top 10

        keyword_section = f"""
KEYWORD OPTIMIZATION STRATEGY:
- REQUIRED SKILLS TO EMPHASIZE: {required_skills_str}
- KEY THEMES TO HIGHLIGHT: {key_themes_str}
- TOP PRIORITY KEYWORDS: {prioritized_str}

STRATEGIC PLACEMENT:
- Place content matching required skills and key themes EARLY in relevant sections
- If job emphasizes "leadership", move leadership accomplishments to TOP of experience bullets
- If job needs "Python", ensure Python experience appears prominently
- Reorder bullets within sections to showcase most relevant achievements first
- Use exact keywords from the job description when replacing text
"""

    prompt = f"""Tailor this resume for the job while preserving formatting. Make strategic replacements using job keywords.

==================================================================================
🚨 RULE #1: PRESERVE ALL QUANTIFIABLE METRICS - ABSOLUTELY CRITICAL 🚨
==================================================================================
⚠️  NEVER REMOVE OR MODIFY ANY NUMBERS, PERCENTAGES, DOLLAR AMOUNTS, OR METRICS!

This is THE MOST IMPORTANT RULE. Quantifiable metrics are the most valuable part of a resume.
If you remove a number, the entire replacement is worthless.

WHAT COUNTS AS QUANTIFIABLE DATA:
- Percentages: 95%, 40%, 2.5%
- Counts: team of 3, 1500+ documents, 5 years
- Dollar amounts: $100K, $5M budget
- Ratios: 70% + 30%, 3:1 ratio
- Growth metrics: 10x improvement, 2x faster
- ANY number that shows impact or scale

PRESERVATION RULES:
- If original has "95%", replacement MUST have "95%" in it
- If original has "team of 3", replacement MUST have "3" somewhere
- If original has "1,500+ documents", replacement MUST have "1,500+"
- If original has TWO numbers like "70% + 30%", keep BOTH numbers
- You can ONLY change the WORDS AROUND the numbers, NEVER the numbers

EXAMPLES OF CORRECT PRESERVATION:
✅ GOOD: "Improved accuracy by 95% through automation" → "Enhanced accuracy by 95% via data analysis"
✅ GOOD: "Led team of 3 engineers" → "Managed 3-person team developing ML solutions"
✅ GOOD: "Processed 1,500+ documents" → "Analyzed 1,500+ documents using NLP"
✅ GOOD: "70% expanded + 30% RAG" → "hybrid pipeline (70% query expansion + 30% RAG)"
✅ GOOD: "$5M budget" → "Managed $5M data infrastructure budget"

EXAMPLES OF VIOLATIONS (DO NOT DO THIS):
❌ BAD: "Improved accuracy by 95%" → "Significantly improved accuracy" (REMOVED 95%)
❌ BAD: "Led team of 3" → "Led cross-functional team" (REMOVED 3)
❌ BAD: "70% expanded + 30% RAG" → "hybrid retrieval approach" (REMOVED percentages)
❌ BAD: "$5M budget" → "Large budget" (REMOVED dollar amount)

IMPORTANT: Only remove metrics if the original text is completely irrelevant to the job and
cannot be reworded to fit. In 99% of cases, you should preserve metrics.
==================================================================================

OTHER CRITICAL RULES:
- Use EXACT text matches from resume (ignore [metadata] annotations)
- Respect char_buffer limits (max chars you can add per line)
- No generic phrases, headers, or contact info changes
- Use <b> and <i> tags for styling if needed
- Max 8-10 high-impact replacements

{format_constraints}

{keyword_section}

WHAT YOU CAN CHANGE (examples using job keywords):
✅ "Developed web applications" → "Built data analysis dashboards" (aligns with Data Scientist role)
✅ "Created automation tools" → "Designed experimentation frameworks" (emphasizes experimentation)
✅ "Improved system performance" → "Enhanced product performance through data-driven insights"
✅ "Analyzed user data" → "Investigated user behavior patterns to guide product decisions"

WHAT YOU CANNOT CHANGE:
❌ Any numbers, percentages, metrics (95%, 3, 1500+, 70%, $100K, 5 years, etc.)
❌ Headers (PROFILE, EDUCATION, EXPERIENCE)
❌ Contact information
❌ Company names, job titles, dates

CHARACTER LIMIT EXAMPLES:
Good: "Developed web apps [char_buffer=12]" → "Developed React apps" (+6 chars, under limit)
Bad: "Managed team [char_buffer=5]" → "Managed cross-functional team" (+20 chars - EXCEEDS limit!)

JSON FORMAT:
{{
    "replacements": [
        {{
            "original_text": "exact text from resume",
            "updated_text": "improved version with keywords",
            "reason": "why this improves fit"
        }}
    ],
    "lines_added": 0,
    "lines_freed": 0,
    "net_lines": 0
}}

RESUME:
{resume_text}

JOB DESCRIPTION:
{job_description}
"""

    try:
        # Increase timeout for complex tailoring operations
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )
        return response.candidates[0].content.parts[0].text
    except Exception as e:
        print(f"Error during AI tailoring: {e}")
        # Return empty replacements on error
        return json.dumps({"replacements": [], "lines_added": 0, "lines_freed": 0, "net_lines": 0})

def tailor_resume_and_return_url(original_resume_url, job_description, job_title, company,
                                   credentials=None, mimikree_email=None, mimikree_password=None, user_full_name=None):
    """Tailor resume and return publicly accessible Google Doc URL

    Args:
        original_resume_url: URL to the original Google Doc resume
        job_description: Job description text
        job_title: Job title
        company: Company name
        credentials: Optional Google OAuth2 Credentials object for user-specific access
        mimikree_email: Optional Mimikree account email for profile integration
        mimikree_password: Optional Mimikree account password for profile integration
        user_full_name: Optional user's full name for document naming
    """
    try:
        # Get Google Services (with user-specific credentials if provided)
        docs_service, drive_service = get_google_services(credentials)
        if not all([docs_service, drive_service]):
            raise ValueError("Failed to authenticate with Google services")

        # Get Doc ID from URL
        original_doc_id = get_doc_id_from_url(original_resume_url)
        if not original_doc_id:
            raise ValueError("Invalid Google Doc URL provided")

        # Get original document name
        try:
            original_doc_name = drive_service.files().get(fileId=original_doc_id, fields='name').execute().get('name')
        except Exception as error:
            print(f"Error fetching document name: {error}")
            original_doc_name = "Resume"

        # Create a copy of the document with custom naming
        if user_full_name:
            # Simple format: FullName_CompanyName
            clean_company = ''.join(c if c.isalnum() else '_' for c in company)
            copied_doc_title = f"{user_full_name}_{clean_company}"
        else:
            copied_doc_title = f"{original_doc_name} - Tailored for {job_title} at {company}"
        print(f"Creating a copy of the document: '{copied_doc_title}'")
        copied_doc_id = copy_google_doc(drive_service, original_doc_id, copied_doc_title)
        if not copied_doc_id:
            raise ValueError("Could not copy the Google Doc")

        # Read content from the ORIGINAL document (optimize by getting document once)
        print("Reading original resume from Google Docs...")
        try:
            document = docs_service.documents().get(documentId=original_doc_id).execute()
        except Exception as e:
            raise ValueError(f"Could not read the Google Doc: {e}")

        # Get both styled and plain text content from the same document
        content = document.get('body', {}).get('content', [])
        original_resume_text = read_structural_elements(content)
        if not original_resume_text:
            raise ValueError("Could not read content from the original Google Doc")

        original_resume_text_plain = read_structural_elements_plain(content)

        # Check if enhanced tailoring is enabled (can be disabled for faster processing)
        use_enhanced_tailoring = os.getenv('USE_ENHANCED_TAILORING', 'true').lower() in ('1', 'true', 'yes')

        if use_enhanced_tailoring:
            print("Using enhanced tailoring with format preservation...")

            # Extract document structure and metadata
            print("Extracting document structure and formatting metadata...")
            try:
                line_metadata = extract_document_structure(docs_service, original_doc_id)
                print(f"Extracted metadata for {len(line_metadata)} lines")

                # Save metadata to file for debugging
                metadata_debug_file = f"../Resumes/metadata_debug_{uuid.uuid4().hex[:8]}.json"
                with open(metadata_debug_file, 'w', encoding='utf-8') as f:
                    json.dump({
                        'total_lines': len(line_metadata),
                        'job_title': job_title,
                        'company': company,
                        'lines': line_metadata
                    }, f, indent=2)
                print(f"📄 Metadata saved to: {metadata_debug_file}")

            except Exception as e:
                print(f"Warning: Structure extraction failed ({e}), using basic tailoring")
                use_enhanced_tailoring = False
                line_metadata = None

            if use_enhanced_tailoring and line_metadata:
                # Calculate line budget (0 additional lines = strict page preservation)
                line_budget = calculate_line_budget(line_metadata, max_additional_lines=0)
                print(f"Line budget: {line_budget['current_paragraphs']} paragraphs, {line_budget['current_visual_lines']} visual lines")
                print(f"⚠️  STRICT MODE: Resume length must stay exactly the same!")

                # Extract keywords from job description (with timeout protection)
                print("Extracting keywords from job description...")
                try:
                    keywords = extract_job_keywords(job_description)
                    print(f"Extracted {len(keywords.get('prioritized_keywords', []))} prioritized keywords")
                    if keywords.get('key_themes'):
                        print(f"Key themes: {', '.join(keywords.get('key_themes', [])[:5])}")
                except Exception as e:
                    print(f"Warning: Keyword extraction failed, continuing without: {e}")
                    keywords = None

                # Mimikree Integration (if credentials provided)
                mimikree_data = None
                mimikree_responses = {}
                if mimikree_email and mimikree_password:
                    print("\n" + "="*60)
                    print("MIMIKREE PROFILE INTEGRATION")
                    print("="*60)

                    try:
                        # Check cache first
                        cached_data = None
                        if SYSTEMATIC_TAILORING_AVAILABLE:
                            cached_data = get_cached_mimikree_data(job_description)

                        if cached_data:
                            print("✅ Using cached Mimikree data")
                            mimikree_data = cached_data.get('formatted_data', '')
                            mimikree_responses = cached_data.get('responses', {})
                        else:
                            # Import Mimikree integration
                            from mimikree_integration import MimikreeClient, generate_questions_from_resume_and_jd

                            # Initialize client
                            print("🔑 Authenticating with Mimikree...")
                            client = MimikreeClient()

                            if client.authenticate(mimikree_email, mimikree_password):
                                print("✅ Mimikree authentication successful!")

                                # Generate questions based on job description
                                print("💬 Generating questions from job description...")
                                questions = generate_questions_from_resume_and_jd(
                                    original_resume_text_plain, 
                                    job_description, 
                                    max_questions=10
                                )

                                if questions:
                                    print(f"📋 Generated {len(questions)} questions")
                                    
                                    # Query Mimikree chatbot
                                    print("💬 Querying Mimikree chatbot for answers...")
                                    response_data = client.ask_batch_questions(questions)

                                    if response_data.get('success'):
                                        # Extract successful question-answer pairs
                                        mimikree_responses = client.extract_successful_answers(response_data)
                                        
                                        if mimikree_responses:
                                            # Format for resume tailoring
                                            formatted_parts = []
                                            for q, a in mimikree_responses.items():
                                                formatted_parts.append(f"Q: {q}\nA: {a}")
                                            mimikree_data = "\n\n".join(formatted_parts)

                                            # Cache for future use
                                            if SYSTEMATIC_TAILORING_AVAILABLE:
                                                cache_data = {
                                                    'responses': mimikree_responses,
                                                    'formatted_data': mimikree_data
                                                }
                                                cache_mimikree_data(job_description, cache_data)
                                                print(f"💾 Cached Mimikree data for future runs")

                                            print(f"✅ Mimikree integration complete!")
                                        else:
                                            print("⚠️ No successful responses from Mimikree chatbot")
                                    else:
                                        print(f"⚠️ Failed to get responses from Mimikree chatbot: {response_data.get('message', 'Unknown error')}")
                                else:
                                    print("⚠️ No questions generated from job description")
                            else:
                                print("⚠️ Mimikree authentication failed - proceeding without profile data")

                    except Exception as e:
                        print(f"⚠️ Mimikree integration failed: {e}")
                        print("   Proceeding without Mimikree data")

                    print("="*60)
                else:
                    print("\n⚠️ Mimikree credentials not provided - skipping profile integration")
                    print("   To use Mimikree integration, provide mimikree_email and mimikree_password")

                # Annotate resume with metadata for AI guidance
                print("\nAnnotating resume with formatting constraints...")
                annotated_resume_text = annotate_resume_with_metadata(original_resume_text, line_metadata)

                # Save annotated resume for debugging
                annotated_debug_file = f"../Resumes/annotated_resume_{uuid.uuid4().hex[:8]}.txt"
                with open(annotated_debug_file, 'w', encoding='utf-8') as f:
                    f.write("=== ANNOTATED RESUME (What Gemini Receives) ===\n\n")
                    f.write(annotated_resume_text)
                print(f"📄 Annotated resume saved to: {annotated_debug_file}")
            else:
                line_budget = None
                keywords = None
                annotated_resume_text = original_resume_text
        else:
            print("Using basic tailoring (set USE_ENHANCED_TAILORING=true for format preservation)...")
            line_metadata = None
            line_budget = None
            keywords = None
            annotated_resume_text = original_resume_text

        # Use systematic tailoring approach (required)
        if not SYSTEMATIC_TAILORING_AVAILABLE:
            raise ImportError("Systematic tailoring modules are required but not available. Please ensure all dependencies are installed.")
        
        use_systematic_tailoring = os.getenv('USE_SYSTEMATIC_TAILORING', 'true').lower() == 'true'
        
        if not use_systematic_tailoring:
            print("⚠️  Systematic tailoring disabled via environment variable. Enabling it anyway (required).")
            use_systematic_tailoring = True

        print("🚀 Starting SYSTEMATIC tailoring...")
        print("="*60)

        # Run systematic tailoring
        try:
            # Conservative mode (default): Only edit Profile → Skills → Projects
            # Aggressive mode: Edit everything including experience bullets
            conservative_mode = os.getenv('CONSERVATIVE_TAILORING', 'true').lower() == 'true'
            
            if conservative_mode:
                print("📊 Mode: CONSERVATIVE (Profile → Skills → Projects only)")
            else:
                print("📊 Mode: AGGRESSIVE (Full resume editing)")
            
            systematic_results = run_systematic_tailoring(
                job_description=job_description,
                job_keywords=keywords.get('prioritized_keywords', [])[:10] if keywords else [],
                line_metadata=line_metadata if line_metadata else [],
                resume_text=original_resume_text_plain,
                mimikree_responses=mimikree_responses if 'mimikree_responses' in locals() else {},
                mimikree_formatted_data=mimikree_data if 'mimikree_data' in locals() and mimikree_data else "",
                conservative_mode=conservative_mode
            )

            # Apply replacements
            if systematic_results['all_replacements']:
                print(f"\n📝 Applying {len(systematic_results['all_replacements'])} replacements...")

                for repl in systematic_results['all_replacements']:
                    try:
                        requests_list = [{
                            'replaceAllText': {
                                'containsText': {
                                    'text': repl['old_text'],
                                    'matchCase': True
                                },
                                'replaceText': repl['new_text']
                            }
                        }]
                        docs_service.documents().batchUpdate(
                            documentId=copied_doc_id,
                            body={'requests': requests_list}
                        ).execute()
                    except Exception as e:
                        print(f"      ⚠️  Skipped: {str(e)[:50]}...")

                print("✅ Replacements applied")

            # CRITICAL: Check page count - only run overflow recovery if needed
            print(f"\n📄 Checking page count...")
            time.sleep(2)  # Wait for changes to settle
            current_page_count = get_actual_page_count(drive_service, copied_doc_id)
            original_page_count = get_actual_page_count(drive_service, original_doc_id)

            print(f"   Original: {original_page_count} page(s)")
            print(f"   Current:  {current_page_count} page(s)")

            # Check for overflow (handle None values from missing PyPDF2)
            if current_page_count is None or original_page_count is None:
                print("   ⚠️  Page count unavailable (PyPDF2 not installed) - skipping overflow check")
            elif current_page_count > original_page_count:
                print(f"\n⚠️  OVERFLOW DETECTED: {current_page_count} pages > {original_page_count} pages")
                
                # Multi-attempt overflow recovery (max 2 attempts)
                max_recovery_attempts = 2
                for attempt in range(1, max_recovery_attempts + 1):
                    if current_page_count <= original_page_count:
                        print(f"✅ Page count resolved!")
                        break
                    
                    print(f"\n🔄 Running overflow recovery (attempt {attempt}/{max_recovery_attempts})...")
                    
                    # Refresh metadata
                    current_line_metadata = extract_document_structure(docs_service, copied_doc_id)
                    
                    # Get the actual lines added from Phase 2 (if available)
                    total_lines_added = 0
                    if systematic_results.get('phase2_results'):
                        total_lines_added = systematic_results['phase2_results'].get('total_lines_added', 0)

                    # Run overflow recovery
                    recovery = recover_from_overflow_if_needed(
                        line_metadata=current_line_metadata,
                        keywords=systematic_results['phase1_results']['feasible_keywords'],
                        current_pages=current_page_count,
                        target_pages=original_page_count,
                        total_lines_added=total_lines_added,
                        attempt=attempt
                    )

                    # Apply recovery replacements
                    replacements = recovery.get('replacements', [])
                    
                    # Check if this is incremental (one-by-one)
                    is_incremental_condense = replacements and replacements[0].get('type') == 'overflow_recovery_condense_incremental'
                    is_incremental_remove = replacements and replacements[0].get('type') == 'overflow_recovery_remove_incremental'
                    is_incremental = is_incremental_condense or is_incremental_remove
                    
                    if is_incremental:
                        action_word = "Condensing" if is_incremental_condense else "Removing"
                        print(f"   Applying {len(replacements)} {action_word.lower()} ONE AT A TIME...")
                        applied_count = 0
                        
                        for i, repl in enumerate(replacements, 1):
                            try:
                                # Apply one bullet condensation or removal
                                requests_list = [{
                                    'replaceAllText': {
                                        'containsText': {'text': repl['old_text'], 'matchCase': True},
                                        'replaceText': repl['new_text']
                                    }
                                }]
                                docs_service.documents().batchUpdate(
                                    documentId=copied_doc_id,
                                    body={'requests': requests_list}
                                ).execute()
                                applied_count += 1
                                
                                # Display appropriate message
                                if is_incremental_condense:
                                    lines_before = repl.get('lines_before', '?')
                                    lines_after = repl.get('lines_after', '?')
                                    print(f"      {i}. Condensed: {repl.get('bullet_text', 'bullet')} ({lines_before}→{lines_after} lines)")
                                else:
                                    print(f"      {i}. Removed: {repl.get('bullet_text', 'bullet')} (relevance: {repl.get('relevance', 0)})")
                                
                                # Check page count after THIS change
                                current_page_count = get_actual_page_count(drive_service, copied_doc_id)
                                
                                if current_page_count <= original_page_count:
                                    print(f"      ✅ Target reached after {action_word.lower()} {applied_count} bullet(s)!")
                                    print(f"      📄 Page count: {current_page_count}/{original_page_count}")
                                    break  # Stop - we've hit the target!
                                    
                            except Exception as e:
                                print(f"      ⚠️ Failed to apply: {str(e)[:50]}...")
                        
                        print(f"   Applied {applied_count}/{len(replacements)} replacements")
                    else:
                        # Apply all at once (non-incremental)
                        applied_count = 0
                        for repl in replacements:
                            try:
                                requests_list = [{
                                    'replaceAllText': {
                                        'containsText': {'text': repl['old_text'], 'matchCase': True},
                                        'replaceText': repl['new_text']
                                    }
                                }]
                                docs_service.documents().batchUpdate(
                                    documentId=copied_doc_id,
                                    body={'requests': requests_list}
                                ).execute()
                                applied_count += 1
                            except Exception as e:
                                print(f"      ⚠️ Failed to apply: {str(e)[:50]}...")
                        
                        print(f"   Applied {applied_count}/{len(replacements)} replacements")
                    
                    # Check page count after recovery
                    time.sleep(2)
                    current_page_count = get_actual_page_count(drive_service, copied_doc_id)
                    print(f"   After recovery: {current_page_count} page(s)")
                    
                    if current_page_count <= original_page_count:
                        print(f"✅ Successfully recovered to {current_page_count} page(s)!")
                        break
                    elif attempt < max_recovery_attempts:
                        print(f"   Still {current_page_count - original_page_count} page(s) over - trying more aggressive approach...")
                    else:
                        print(f"⚠️  Could not fully recover - still {current_page_count - original_page_count} page(s) over after {max_recovery_attempts} attempts")
                        
            else:
                print(f"✅ No overflow - resume stays at {current_page_count} page(s)")

            print(f"\n{'='*60}")
            print("✅ SYSTEMATIC TAILORING COMPLETE")
            print(f"{'='*60}")
            print(f"   Match Rate: {systematic_results['phase1_results']['match_percentage']:.1f}%")
            print(f"   Feasible Keywords: {len(systematic_results['phase1_results']['feasible_keywords'])}")
            print(f"   Max Possible ATS: {systematic_results['phase1_results']['max_possible_ats_score']}/100")
            print(f"   Final Pages: {current_page_count}")
            print(f"{'='*60}")

        except Exception as e:
            print(f"❌ Systematic tailoring failed: {e}")
            import traceback
            traceback.print_exc()
            raise

        # Make the document publicly accessible
        print("Making document publicly accessible...")
        permission = {
            'role': 'reader',
            'type': 'anyone'
        }
        
        drive_service.permissions().create(
            fileId=copied_doc_id,
            body=permission
        ).execute()

        # Also download the final resume as a PDF (for backup)
        print("Downloading the tailored resume as a PDF...")
        output_pdf_path = f"../Resumes/{copied_doc_title}.pdf"
        download_doc_as_pdf(drive_service, copied_doc_id, output_pdf_path)

        # Prepare tailoring metrics for frontend
        # Get keyword analysis results
        feasible_keywords = systematic_results['phase1_results']['feasible_keywords']
        missing_keywords = systematic_results['phase1_results']['missing_keywords']
        all_job_keywords = keywords.get('prioritized_keywords', [])[:15] if keywords else []
        
        # Calculate which keywords were already present vs newly added
        # Check original resume text to see which keywords were already there
        already_present_keywords = []
        newly_added_keywords = []
        
        original_resume_lower = original_resume_text_plain.lower()
        for keyword in feasible_keywords:
            keyword_lower = keyword.lower()
            # Check for exact match or close variations
            if (keyword_lower in original_resume_lower or 
                any(word in original_resume_lower for word in keyword_lower.split()) or
                # Check for common variations
                keyword_lower.replace(' ', '') in original_resume_lower.replace(' ', '')):
                already_present_keywords.append(keyword)
            else:
                newly_added_keywords.append(keyword)
        
        tailoring_metrics = {
            'url': f"https://docs.google.com/document/d/{copied_doc_id}/edit",
            'pdf_path': output_pdf_path,
            'keywords': {
                'total_extracted': len(all_job_keywords),
                'prioritized_list': all_job_keywords,
                'job_required': all_job_keywords,
                'already_present': already_present_keywords,
                'newly_added': newly_added_keywords,
                'could_not_add': missing_keywords,
                'feasible': feasible_keywords,  # Keep for backward compatibility
                'missing': missing_keywords,    # Keep for backward compatibility
                'evidence_summary': systematic_results['phase1_results'].get('evidence_summary', {})
            },
            'match_stats': {
                'match_percentage': systematic_results['phase1_results']['match_percentage'],
                'total_required': len(all_job_keywords),
                'already_had': len(already_present_keywords),
                'added': len(newly_added_keywords),
                'missing': len(missing_keywords),
                'feasible_count': len(feasible_keywords),  # Keep for backward compatibility
                'missing_count': len(missing_keywords),    # Keep for backward compatibility
                'max_possible_ats_score': systematic_results['phase1_results']['max_possible_ats_score']
            },
            'sections_modified': {
                'profile': any(r.get('type') == 'profile_rewrite' for r in systematic_results['all_replacements']),
                'skills': any(r.get('type') in ['skills_reorg', 'skills_intelligent_update'] for r in systematic_results['all_replacements']),
                'projects': any(r.get('type') in ['project_bullet_enhance'] for r in systematic_results['all_replacements'])
            },
            'page_stats': {
                'original_pages': original_page_count,
                'final_pages': current_page_count,
                'overflow_recovered': current_page_count <= original_page_count
            },
            'replacements_applied': len(systematic_results['all_replacements'])
        }

        print(f"✅ Tailored resume created and made public: {tailoring_metrics['url']}")
        
        # Debug: Print metrics structure for troubleshooting
        print("\n📊 TAILORING METRICS GENERATED:")
        print(f"   Keywords - Job Required: {len(tailoring_metrics['keywords']['job_required'])}")
        print(f"   Keywords - Already Present: {len(tailoring_metrics['keywords']['already_present'])}")
        print(f"   Keywords - Newly Added: {len(tailoring_metrics['keywords']['newly_added'])}")
        print(f"   Keywords - Could Not Add: {len(tailoring_metrics['keywords']['could_not_add'])}")
        print(f"   Match Percentage: {tailoring_metrics['match_stats']['match_percentage']:.1f}%")
        print(f"   Sections Modified: Profile={tailoring_metrics['sections_modified']['profile']}, Skills={tailoring_metrics['sections_modified']['skills']}, Projects={tailoring_metrics['sections_modified']['projects']}")
        print(f"   Replacements Applied: {tailoring_metrics['replacements_applied']}")
        
        return tailoring_metrics

    except Exception as e:
        print(f"Error in tailor_resume_and_return_url: {e}")
        raise
    
if __name__ == "__main__":
    # Example usage - replace with your actual values
    google_doc_url = "https://docs.google.com/document/d/1flfyzOJ_5sOklftoq76HLErYDmEYYdOsEsAB4G4ZMIs/edit?tab=t.0"
    job_description = """
    Figma is growing our team of passionate creatives and builders on a mission to make design accessible to all. Figma’s platform helps teams bring ideas to life—whether you're brainstorming, creating a prototype, translating designs into code, or iterating with AI. From idea to product, Figma empowers teams to streamline workflows, move faster, and work together in real time from anywhere in the world. If you're excited to shape the future of design and collaboration, join us!

We’re looking for Data Science Interns who are excited to use data to answer big questions and guide decisions across Figma. At Figma, interns are embedded into small, collaborative teams where they’ll partner closely with engineers, PMs, and designers to make sense of data, build models, and surface insights that shape our product and business.

This internship will be based out of our San Francisco or New York hub.

What you’ll do at Figma:
Collaborate across teams to turn business questions into data problems
Design experiments and evaluate metrics to guide product decisions
Build models or conduct analyzes that help us understand behavior and growth
Develop tools, datasets, or dashboards that make data more accessible to others
Share insights with both technical and non-technical teammates
Some projects you could work on:

Investigate user behavior and recommend product improvements
Improve experimentation accuracy and velocity through new testing methodologies
Build internal datasets and models to support product, marketing, or business teams
Explore growth opportunities through critical metric analysis and funnel research
We’d love to hear from you if you have:

Investigate user behavior and recommend product improvements
Improve experimentation accuracy and velocity through new testing methodologies
Build internal datasets and models to support product, marketing, or business teams
Explore growth opportunities through critical metric analysis and funnel research
At Figma, one of our values is Grow as you go. We believe in hiring smart, curious people who are excited to learn and develop their skills. If you’re excited about this role but your past experience doesn’t align perfectly with the points outlined in the job description, we encourage you to apply anyways. You may be just the right candidate for this or other roles.

#LI-HO1
Pay Transparency Disclosure

This internship role is based in either Figma’s San Francisco or New York hub offices, and has the hourly base pay rate stated below.  Figma also offers interns a housing stipend and travel reimbursement. Figma’s compensation and benefits are subject to change and may be modified in the future.

Internship

$44.71 - $44.71 USD

At Figma we celebrate and support our differences. We know employing a team rich in diverse thoughts, experiences, and opinions allows our employees, our product and our community to flourish. Figma is an equal opportunity workplace - we are dedicated to equal employment opportunities regardless of race, color, ancestry, religion, sex, national origin, sexual orientation, age, citizenship, marital status, disability, gender identity/expression, veteran status, or any other characteristic protected by law. We also consider qualified applicants regardless of criminal histories, consistent with legal requirements.

We will work to ensure individuals with disabilities are provided reasonable accommodation to apply for a role, participate in the interview process, perform essential job functions, and receive other benefits and privileges of employment. If you require accommodation, please reach out to accommodations-ext@figma.com. These modifications enable an individual with a disability to have an equal opportunity not only to get a job, but successfully perform their job tasks to the same extent as people without disabilities. 

Examples of accommodations include but are not limited to: 

Holding interviews in an accessible location
Enabling closed captioning on video conferencing
Ensuring all written communication be compatible with screen readers
Changing the mode or format of interviews 
To ensure the integrity of our hiring process and facilitate a more personal connection, we require all candidates keep their cameras on during video interviews. Additionally, if hired you will be required to attend in person onboarding.

By applying for this job, the candidate acknowledges and agrees that any personal data contained in their application or supporting materials will be processed in accordance with Figma's Candidate Privacy Notice.
    """
    job_title = "Data Scientist"
    company = "Figma"
    
    try:
        tailored_url = tailor_resume_and_return_url(google_doc_url, job_description, job_title, company)
        print(f"Tailored resume created: {tailored_url}")
    except Exception as e:
        print(f"Error: {e}")

