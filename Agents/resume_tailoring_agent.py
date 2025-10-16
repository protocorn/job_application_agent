import os
import time
import uuid
import re
import json
import unicodedata
from google import genai
import dotenv
from typing import List, Tuple
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


dotenv.load_dotenv()

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

def _validate_replacements(raw_replacements):
    valid = []
    invalid = []
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

    bold_ranges = find_tag_ranges(doc_text, '<b>', '</b>')
    italic_ranges = find_tag_ranges(doc_text, '<i>', '</i>')
    

    format_requests = []
    # Apply bold first
    for idx, (start, end) in enumerate(bold_ranges):
        if end <= start:
            continue
        
        # Optional shift fix for known numeric-left-shift issue
        shift_fix = 0
        if os.getenv('BOLD_SHIFT_DIGITS_PLUS_ONE', 'false').lower() in ('1','true','yes'):
            snippet_local = doc_text[start:end]
            digit_count = sum(1 for ch in snippet_local if ch.isdigit())
            if digit_count > 0:
                shift_fix = digit_count + 1
        # Include trailing '%' if present in snippet
        extra_end = 0
        if doc_text[start:end].endswith('%'):
            extra_end = 1
        s_idx = start + 1 + shift_fix
        e_idx = end + 1 + shift_fix + extra_end
        # Clamp end index to document length (Docs API endIndex is exclusive)
        max_end = len(doc_text) + 1
        if e_idx > max_end:
            e_idx = max_end
        format_requests.append({
            'updateTextStyle': {
                'range': {
                    'startIndex': s_idx,
                    'endIndex': e_idx
                },
                'textStyle': {
                    'bold': True
                },
                'fields': 'bold'
            }
        })
    # Then italic
    for idx, (start, end) in enumerate(italic_ranges):
        if end <= start:
            continue
        format_requests.append({
            'updateTextStyle': {
                'range': {
                    'startIndex': start + 1,
                    'endIndex': end + 1
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
        print(f"Applied tag-based style batch {i//batch_size + 1}")

    if strip_tags:
        # Remove tags everywhere
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

        batch_size = 15
        for i in range(0, len(cleanup_requests), batch_size):
            batch = cleanup_requests[i:i + batch_size]
            if not batch:
                continue
            docs_service.documents().batchUpdate(
                documentId=document_id,
                body={'requests': batch}
            ).execute()
            print(f"Stripped tag batch {i//batch_size + 1}")

    # Optional verification: if tags were stripped, pause briefly to allow styles to settle
    if strip_tags:
        time.sleep(2)


def _regenerate_invalid_replacements(client, resume_text, job_description, invalid_items):
    if not invalid_items:
        return []
    # Ask model to only regenerate updated_text for the specified original_text entries
    originals_list = "\n".join([f"- {item['original_text']}" for item in invalid_items])
    prompt = f"""
You previously proposed replacements for tailoring a resume, but some items violated constraints
(generic language, headers, newlines, or low-value changes). For the list below, regenerate ONLY the
updated_text values. Keep original_text exactly the same.

Constraints:
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

def apply_json_replacements_to_doc(docs_service, document_id, replacements_json, resume_text=None, job_description_text=None, keep_tags_literal=None):
    """Applies text replacements from JSON to Google Doc while preserving all formatting."""
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
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON response: {e}")
            print(f"Raw response: {replacements_json}")
            return
        
        if not replacements:
            print("No replacements needed - resume is already well-tailored!")
            return
        
        print(f"Found {len(replacements)} text replacements from model")

        # Validate and regenerate invalid items
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        valid, invalid = _validate_replacements(replacements)
        
        print(f"🔍 Validation Results: {len(valid)} valid, {len(invalid)} invalid replacements")
        
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
                    # Tag-first flow: insert updated_text with literal tags, then apply styles from tags, then strip tags
                    if style_ranges:
                        api_requests.append({
                            'replaceAllText': {
                                'containsText': {
                                    'text': plain_original_text,
                                    'matchCase': True
                                },
                                'replaceText': updated_text
                            }
                        })
                        # We will not create markers here; tags will be parsed from the doc itself post-replace
                        print(f"Queued tag-first replace for: '{plain_original_text[:60]}...' with {len(style_ranges)} style ranges")
                    else:
                        # No styles, plain replacement
                        api_requests.append({
                            'replaceAllText': {
                                'containsText': {
                                    'text': plain_original_text,
                                    'matchCase': True
                                },
                                'replaceText': plain_updated_text
                            }
                        })
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

        # Apply text replacements first in batches [[memory:5605113]]
        batch_size = 10
        for i in range(0, len(api_requests), batch_size):
            batch = api_requests[i:i + batch_size]
            if batch:
                docs_service.documents().batchUpdate(
                    documentId=document_id,
                    body={'requests': batch}
                ).execute()
                print(f"Applied batch {i//batch_size + 1}")

        # Apply styling
        if keep_tags_literal:
            print("KEEP_TAGS_LITERAL=true: Applying style from literal tags and keeping tags visible.")
            _apply_styles_from_tags(docs_service, document_id, strip_tags=False)
        else:
            # Prefer tag-first flow if any tags exist in the current doc after replacement
            # We'll attempt tag-based styling/stripping; if there are no tags, nothing will happen
            _apply_styles_from_tags(docs_service, document_id, strip_tags=True)

        print("✓ Text replacements applied with replaceAllText while preserving formatting!")
        
    except HttpError as error:
        print(f"An error occurred while applying changes: {error}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        import traceback
        traceback.print_exc()

def download_doc_as_pdf(drive_service, doc_id, pdf_path):
    """Downloads a Google Doc as a PDF file."""
    try:
        request = drive_service.files().export_media(fileId=doc_id, mimeType='application/pdf')
        with open(pdf_path, 'wb') as f:
            f.write(request.execute())
        print(f"Successfully downloaded tailored resume to {pdf_path}")
    except HttpError as error:
        print(f"An error occurred during PDF download: {error}")

def tailor_resume(resume_text, job_description):
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    prompt = f"""
You are an expert resume writer specializing in tailoring resumes for specific job applications.

Your task: Analyze the resume and job description, then suggest ONLY high-quality, strategic text replacements that will better align the resume with the job requirements.

QUALITY STANDARDS:
- Only suggest replacements that significantly improve job relevance
- Keep the professional tone and avoid repetitive phrases
- Incorporate specific keywords from the job description naturally
- DO NOT add generic phrases like "showcasing ability to..." or "demonstrating..."
- DO NOT modify headers, section titles, or contact information
- Focus on concrete improvements that highlight relevant skills/experience

TECHNICAL REQUIREMENTS:
- "original_text" must be EXACT text from the resume (word-for-word match)
- Do not include bullet points, formatting, or section headers
- Only replace complete sentences or meaningful phrases
- Maximum 5-8 strategic replacements for best results
- You can use <b>text</b> for bold and <i>text</i> for italic styling in updated_text if needed or matches the style of original_text.



Return your response in this JSON format:
{{
    "replacements": [
        {{
            "original_text": "exact text from resume to replace",
            "updated_text": "improved version with job-relevant keywords"
        }}
    ]
}}

If the resume already aligns well, return fewer high-impact replacements rather than many minor ones.

RESUME:
{resume_text}

JOB DESCRIPTION:
{job_description}
"""
    
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )
    return response.candidates[0].content.parts[0].text

def tailor_resume_and_return_url(original_resume_url, job_description, job_title, company, credentials=None):
    """Tailor resume and return publicly accessible Google Doc URL

    Args:
        original_resume_url: URL to the original Google Doc resume
        job_description: Job description text
        job_title: Job title
        company: Company name
        credentials: Optional Google OAuth2 Credentials object for user-specific access
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

        # Create a copy of the document
        copied_doc_title = f"{original_doc_name} - Tailored for {job_title} at {company}"
        print(f"Creating a copy of the document: '{copied_doc_title}'")
        copied_doc_id = copy_google_doc(drive_service, original_doc_id, copied_doc_title)
        if not copied_doc_id:
            raise ValueError("Could not copy the Google Doc")

        # Read content from the ORIGINAL document
        print("Reading original resume from Google Docs...")
        original_resume_text = read_google_doc_content(docs_service, original_doc_id)
        if original_resume_text is None:
            raise ValueError("Could not read content from the original Google Doc")
        
        # Also get plain text version for internal operations
        document = docs_service.documents().get(documentId=original_doc_id).execute()
        original_resume_text_plain = read_structural_elements_plain(document.get('body').get('content'))
        
        # Get tailoring suggestions from Gemini
        print("Getting tailoring suggestions from Gemini...")
        replacements_json = tailor_resume(original_resume_text, job_description)
        print("Tailoring suggestions received successfully.")

        # Apply JSON-based replacements to the COPIED document
        print("Applying tailoring changes to the copied document...")
        apply_json_replacements_to_doc(docs_service, copied_doc_id, replacements_json, original_resume_text, job_description)

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

        # Return the public URL
        tailored_doc_url = f"https://docs.google.com/document/d/{copied_doc_id}/edit"
        print(f"✅ Tailored resume created and made public: {tailored_doc_url}")
        return tailored_doc_url

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

