"""
Google Docs to PDF converter utility.
Handles converting Google Docs URLs to downloadable PDFs.
"""
import os
import re
import requests
from loguru import logger


class GoogleDocsConverter:
    """Converts Google Docs URLs to PDF files."""

    @staticmethod
    def is_google_docs_url(url: str) -> bool:
        """Check if URL is a Google Docs link."""
        if not url:
            return False

        google_patterns = [
            r'docs\.google\.com/document',
            r'drive\.google\.com/file',
            r'docs\.google\.com/file'
        ]

        return any(re.search(pattern, url) for pattern in google_patterns)

    @staticmethod
    def extract_document_id(url: str) -> str:
        """Extract document ID from Google Docs/Drive URL."""
        # Pattern for docs.google.com/document/d/{ID}
        doc_match = re.search(r'/document/d/([a-zA-Z0-9-_]+)', url)
        if doc_match:
            return doc_match.group(1)

        # Pattern for drive.google.com/file/d/{ID}
        drive_match = re.search(r'/file/d/([a-zA-Z0-9-_]+)', url)
        if drive_match:
            return drive_match.group(1)

        # Pattern for docs.google.com/file/d/{ID}
        file_match = re.search(r'/file/d/([a-zA-Z0-9-_]+)', url)
        if file_match:
            return file_match.group(1)

        return None

    @staticmethod
    def get_pdf_export_url(doc_id: str) -> str:
        """Get the PDF export URL for a Google Docs document."""
        return f"https://docs.google.com/document/d/{doc_id}/export?format=pdf"

    @staticmethod
    def _download_as_pdf_with_oauth(url: str, output_path: str, user_id: str) -> bool:
        """
        Download Google Doc as PDF using stored OAuth credentials.

        Strategy:
        1. Try to use google_oauth_service directly (works when running from source /
           a dev environment where the server/ directory is present).
        2. If that import fails (e.g. when running from the installed PyPI package),
           fall back to the Launchway server API endpoint which holds the OAuth
           credentials and performs the export server-side.
        """
        # ── Strategy 1: direct server-side OAuth (source / dev environment) ──────
        try:
            doc_id = GoogleDocsConverter.extract_document_id(url)
            if not doc_id:
                msg = f"[ERROR] Could not extract document ID from URL: {url}"
                print(msg)
                logger.error(msg)
                return False

            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
            server_dir = os.path.join(project_root, 'server')
            import sys
            if os.path.isdir(server_dir) and server_dir not in sys.path:
                sys.path.append(server_dir)

            from google_oauth_service import GoogleOAuthService  # raises ImportError when not on server
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaIoBaseDownload
            import io

            credentials = GoogleOAuthService.get_credentials(str(user_id))
            if not credentials:
                msg = (
                    "[ERROR] Resume PDF download failed - Google Doc is private "
                    "and no valid Google account credentials are stored.\n"
                    "  → Open the app → Profile → Resume → 'Connect Google Account'\n"
                    "  → Or set document sharing to 'Anyone with the link can view' in Google Docs"
                )
                print(msg)
                logger.error(msg)
                return False

            drive_service = build('drive', 'v3', credentials=credentials)
            req = drive_service.files().export_media(fileId=doc_id, mimeType='application/pdf')
            file_stream = io.BytesIO()
            downloader = MediaIoBaseDownload(file_stream, req)
            done = False
            while not done:
                _, done = downloader.next_chunk()

            file_bytes = file_stream.getvalue()
            if not file_bytes:
                msg = "[ERROR] OAuth PDF export returned empty content"
                print(msg)
                logger.error(msg)
                return False

            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, 'wb') as f:
                f.write(file_bytes)

            logger.info(f"✅ PDF downloaded via local OAuth: {output_path} ({len(file_bytes)} bytes)")
            return True

        except ImportError:
            # google_oauth_service is not available locally (installed package).
            # Fall back to the server API which has the credentials.
            logger.info("google_oauth_service not available locally - using server API for PDF export")

        except Exception as e:
            msg = f"[ERROR] Local OAuth PDF export failed: {e}"
            print(msg)
            logger.error(msg)
            return False

        # ── Strategy 2: server API fallback (installed package) ──────────────────
        try:
            from launchway.session import load_session
            from launchway.api_client import LaunchwayClient

            token, _ = load_session()
            if not token:
                msg = (
                    "[ERROR] Resume PDF download failed - not logged in.\n"
                    "  → Run: launchway login"
                )
                print(msg)
                logger.error(msg)
                return False

            print("  Requesting resume PDF from server (using your connected Google account)...")
            client = LaunchwayClient(token=token)
            if client.download_resume_pdf(output_path, resume_url=url):
                logger.info(f"✅ PDF downloaded via server API: {output_path}")
                return True

            # download_resume_pdf already printed a specific error message
            return False

        except Exception as e:
            msg = f"[ERROR] Server API PDF download failed: {e}"
            print(msg)
            logger.error(msg)
            return False

    @staticmethod
    def download_as_pdf(url: str, output_path: str, user_id: str = None) -> bool:
        """
        Download Google Docs URL as PDF.

        Args:
            url: Google Docs URL
            output_path: Where to save the PDF

        Returns:
            True if successful, False otherwise
        """
        try:
            # Extract document ID
            doc_id = GoogleDocsConverter.extract_document_id(url)
            if not doc_id:
                msg = f"[ERROR] Could not extract document ID from URL: {url}"
                print(msg)
                logger.error(msg)
                return False

            # Get PDF export URL
            pdf_url = GoogleDocsConverter.get_pdf_export_url(doc_id)
            print(f"  Downloading resume PDF (doc: {doc_id})...")
            logger.info(f"📥 Downloading Google Docs as PDF: {doc_id}")

            # Download the PDF via public export first (works for public/shared docs)
            response = requests.get(pdf_url, timeout=30)
            response.raise_for_status()

            # Check if we got a PDF (not an HTML login/redirect page)
            content_type = response.headers.get('Content-Type', '')
            if 'application/pdf' not in content_type:
                logger.warning(
                    f"Public export returned non-PDF content ({content_type}) - doc is likely private"
                )
                if user_id:
                    print("  Resume Google Doc appears to be private - trying OAuth (connected Google account)...")
                    logger.info("🔑 Trying OAuth export fallback...")
                    if GoogleDocsConverter._download_as_pdf_with_oauth(url, output_path, user_id):
                        return True
                    # OAuth also failed - the detailed reason was already printed inside _download_as_pdf_with_oauth
                    msg = (
                        "[ERROR] Resume PDF conversion failed - Google Doc is private and OAuth export failed.\n"
                        "  → Try reconnecting your Google account in the app, or check that it has access to this document."
                    )
                    print(msg)
                    logger.error(msg)
                else:
                    msg = (
                        "[ERROR] Resume PDF conversion failed - Google Doc appears to be private.\n"
                        "  → Connect your Google account: open the app → Profile → Resume → 'Connect Google Account'\n"
                        "  → Or set document sharing to 'Anyone with the link can view' in Google Docs"
                    )
                    print(msg)
                    logger.error(msg)
                return False

            # Save to file
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, 'wb') as f:
                f.write(response.content)

            file_size = len(response.content)
            logger.info(f"✅ PDF downloaded successfully: {output_path} ({file_size} bytes)")
            return True

        except requests.exceptions.RequestException as e:
            logger.warning(f"Public Google Docs PDF download failed: {e}")
            if user_id:
                print("  Public resume download failed - trying OAuth fallback...")
                if GoogleDocsConverter._download_as_pdf_with_oauth(url, output_path, user_id):
                    return True
            msg = f"[ERROR] Failed to download resume PDF: {e}"
            print(msg)
            logger.error(msg)
            return False
        except Exception as e:
            msg = f"[ERROR] Unexpected error downloading resume PDF: {e}"
            print(msg)
            logger.error(msg)
            return False

    @staticmethod
    def convert_to_pdf_if_needed(resume_url_or_path: str, resumes_dir: str = None, user_id: str = None) -> str:
        """
        Convert resume to PDF if it's a Google Docs URL, otherwise return original path.

        Args:
            resume_url_or_path: Either a local file path or Google Docs URL
            resumes_dir: Directory to save converted PDFs (default: Resumes/)

        Returns:
            Path to PDF file (either original or newly downloaded), or None on failure
        """
        # If it's not a Google Docs URL, return as-is
        if not GoogleDocsConverter.is_google_docs_url(resume_url_or_path):
            if os.path.exists(resume_url_or_path):
                return resume_url_or_path
            else:
                logger.warning(f"Resume path does not exist: {resume_url_or_path}")
                return resume_url_or_path

        # Determine output directory
        if resumes_dir is None:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
            resumes_dir = os.path.join(project_root, 'Resumes')

        # Generate output filename
        doc_id = GoogleDocsConverter.extract_document_id(resume_url_or_path)
        output_filename = f"resume_{doc_id}.pdf"
        output_path = os.path.join(resumes_dir, output_filename)

        # Check if already downloaded (cached)
        if os.path.exists(output_path):
            print(f"  Resume PDF already cached: {output_filename}")
            logger.info(f"✅ PDF already exists: {output_path}")
            return output_path

        # It's a Google Docs URL and not cached - convert to PDF
        print(f"  Converting Google Doc resume to PDF...")
        logger.info(f"🔄 Detected Google Docs URL, converting to PDF...")

        # Download the PDF
        if GoogleDocsConverter.download_as_pdf(resume_url_or_path, output_path, user_id=user_id):
            print(f"  ✓ Resume PDF ready: {output_filename}")
            return output_path
        else:
            # Specific failure reason was already printed inside download_as_pdf
            logger.error(f"❌ Failed to convert Google Docs to PDF")
            return None
