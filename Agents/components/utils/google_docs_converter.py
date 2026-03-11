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
        This path supports private docs and tailored docs owned by the user.
        """
        try:
            doc_id = GoogleDocsConverter.extract_document_id(url)
            if not doc_id:
                logger.error(f"Could not extract document ID from URL: {url}")
                return False

            # Ensure server modules are importable from local CLI runtime.
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
            server_dir = os.path.join(project_root, 'server')
            import sys
            if os.path.isdir(server_dir) and server_dir not in sys.path:
                sys.path.append(server_dir)

            from google_oauth_service import GoogleOAuthService
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaIoBaseDownload
            import io

            credentials = GoogleOAuthService.get_credentials(str(user_id))
            if not credentials:
                logger.warning("No valid Google OAuth credentials found for user")
                return False

            drive_service = build('drive', 'v3', credentials=credentials)
            request = drive_service.files().export_media(fileId=doc_id, mimeType='application/pdf')
            file_stream = io.BytesIO()
            downloader = MediaIoBaseDownload(file_stream, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

            file_bytes = file_stream.getvalue()
            if not file_bytes:
                logger.error("OAuth export returned empty PDF content")
                return False

            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, 'wb') as f:
                f.write(file_bytes)

            logger.info(f"✅ PDF downloaded via OAuth: {output_path} ({len(file_bytes)} bytes)")
            return True
        except Exception as e:
            logger.error(f"❌ OAuth PDF export failed: {e}")
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
                logger.error(f"Could not extract document ID from URL: {url}")
                return False

            # Get PDF export URL
            pdf_url = GoogleDocsConverter.get_pdf_export_url(doc_id)
            logger.info(f"📥 Downloading Google Docs as PDF: {doc_id}")

            # Download the PDF via public export first (works for public/shared docs)
            response = requests.get(pdf_url, timeout=30)
            response.raise_for_status()

            # Check if we got a PDF (not an error page)
            content_type = response.headers.get('Content-Type', '')
            if 'application/pdf' not in content_type:
                logger.error(f"Response is not a PDF. Content-Type: {content_type}")
                logger.warning("Public export did not return PDF, trying OAuth export fallback...")
                if user_id and GoogleDocsConverter._download_as_pdf_with_oauth(url, output_path, user_id):
                    return True
                logger.error("Make sure the document is publicly accessible or connect Google OAuth in the app")
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
            if user_id and GoogleDocsConverter._download_as_pdf_with_oauth(url, output_path, user_id):
                return True
            logger.error("❌ Failed to download Google Docs PDF (public + OAuth fallback)")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error downloading PDF: {e}")
            return False

    @staticmethod
    def convert_to_pdf_if_needed(resume_url_or_path: str, resumes_dir: str = None, user_id: str = None) -> str:
        """
        Convert resume to PDF if it's a Google Docs URL, otherwise return original path.

        Args:
            resume_url_or_path: Either a local file path or Google Docs URL
            resumes_dir: Directory to save converted PDFs (default: Resumes/)

        Returns:
            Path to PDF file (either original or newly downloaded)
        """
        # If it's not a Google Docs URL, return as-is
        if not GoogleDocsConverter.is_google_docs_url(resume_url_or_path):
            # Check if it's a valid file path
            if os.path.exists(resume_url_or_path):
                return resume_url_or_path
            else:
                logger.warning(f"Resume path does not exist: {resume_url_or_path}")
                return resume_url_or_path

        # It's a Google Docs URL - convert to PDF
        logger.info(f"🔄 Detected Google Docs URL, converting to PDF...")

        # Determine output directory
        if resumes_dir is None:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
            resumes_dir = os.path.join(project_root, 'Resumes')

        # Generate output filename
        doc_id = GoogleDocsConverter.extract_document_id(resume_url_or_path)
        output_filename = f"resume_{doc_id}.pdf"
        output_path = os.path.join(resumes_dir, output_filename)

        # Check if already downloaded
        if os.path.exists(output_path):
            logger.info(f"✅ PDF already exists: {output_path}")
            return output_path

        # Download the PDF
        if GoogleDocsConverter.download_as_pdf(resume_url_or_path, output_path, user_id=user_id):
            return output_path
        else:
            logger.error(f"❌ Failed to convert Google Docs to PDF")
            return None
