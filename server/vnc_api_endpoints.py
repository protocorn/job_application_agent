"""
VNC API Endpoints for Backend

Provides REST API endpoints for managing VNC browser sessions
"""

import logging
import asyncio
import sys
import os
from datetime import datetime
from flask import Blueprint, request, jsonify

# Add paths
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'Agents'))

from Agents.components.vnc import vnc_session_manager
from Agents.job_application_agent import run_links_with_refactored_agent
from Agents.components.session.session_manager import SessionManager
from auth import require_auth
from batch_vnc_manager import batch_vnc_manager
from dev_browser_session import dev_browser_session
from vnc_stream_proxy import register_vnc_session, unregister_vnc_session
from profile_service import ProfileService
import tempfile
import requests
import os

logger = logging.getLogger(__name__)

# Helper to download resume
def _download_resume_to_temp(resume_url):
    """Download resume from URL to a temporary file"""
    try:
        if not resume_url:
            return None
        
        # Basic validation
        if not resume_url.startswith('http'):
            return None
            
        # Handle Google Docs export
        if "docs.google.com/document/d/" in resume_url:
            try:
                doc_id = resume_url.split("/d/")[1].split("/")[0]
                resume_url = f"https://docs.google.com/document/d/{doc_id}/export?format=pdf"
                logger.info(f"Converted Google Doc URL to PDF export: {resume_url}")
            except:
                pass
            
        logger.info(f"Downloading resume from {resume_url}...")
        response = requests.get(resume_url, timeout=15)
        
        if response.status_code == 200:
            # Create temp file
            # We don't delete immediately; agent needs to read it.
            # OS /tmp cleaning or restart will handle it eventually.
            # In a better implementation, we'd track and delete.
            fd, path = tempfile.mkstemp(suffix='.pdf', prefix='resume_')
            with os.fdopen(fd, 'wb') as f:
                f.write(response.content)
            logger.info(f"Resume downloaded to {path}")
            return path
        else:
            logger.error(f"Failed to download resume: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Error downloading resume: {e}")
        return None

# Create Blueprint
vnc_api = Blueprint('vnc_api', __name__)

# Global session manager for persistence
session_manager = SessionManager(storage_dir="sessions")

# Check if we're in development mode (Windows - no VNC available)
import os
import platform
IS_WINDOWS = platform.system() == 'Windows'
IS_DEVELOPMENT = os.getenv('FLASK_ENV') == 'development' or IS_WINDOWS

if IS_DEVELOPMENT and IS_WINDOWS:
    logger.info("🪟 Running on Windows - using dev browser sessions (VNC not available)")
    logger.info("   VNC will work when deployed to Railway (Linux)")
else:
    logger.info("🐧 Running on Linux - VNC fully available")


@vnc_api.route("/api/vnc/apply-job", methods=['POST'])
@require_auth
def apply_job_with_vnc():
    """
    Start job application with VNC streaming enabled
    
    Request body:
    {
        "jobUrl": "https://job-url.com",
        "resumeUrl": "https://resume-url.com",
        "userId": "user-id"
    }
    
    Response:
    {
        "success": true,
        "session_id": "uuid",
        "vnc_port": 5900,
        "websocket_url": "wss://your-backend.railway.app/vnc-stream/uuid",
        "message": "VNC session started"
    }
    """
    try:
        data = request.json
        job_url = data.get('jobUrl')
        # Get resume URL from request or profile
        resume_url = data.get('resumeUrl')
        user_id = request.current_user['id']
        
        if not job_url:
            return jsonify({"error": "jobUrl is required"}), 400
            
        # If resume not provided, fetch from profile
        if not resume_url:
            try:
                profile = ProfileService.get_profile(user_id)
                if profile:
                    resume_url = profile.get('resume_url')
            except Exception as e:
                logger.warning(f"Could not fetch profile for user {user_id}: {e}")
        
        # Download resume to temp file for injection
        resume_path = _download_resume_to_temp(resume_url)
        
        logger.info(f"🎬 Starting VNC job application for user {user_id}")
        logger.info(f"   Job URL: {job_url}")
        logger.info(f"   Resume: {resume_path if resume_path else 'None'}")
        
        # Generate session ID
        import uuid
        session_id = str(uuid.uuid4())
        
        # Start the job application in background with VNC
        import threading
        
        def run_agent_async():
            """Run agent in background thread"""
            try:
                # Create new event loop for this thread
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                logger.info(f"🤖 Starting agent with VNC for session {session_id}")
                
                # Run agent with VNC mode enabled
                vnc_info = loop.run_until_complete(
                    run_links_with_refactored_agent(
                        links=[job_url],
                        headless=False,  # Must be False for VNC
                        keep_open=True,  # Keep browser open for user
                        debug=False,
                        hold_seconds=0,
                        slow_mo_ms=0,
                        job_id=session_id,
                        jobs_dict={},
                        session_manager=session_manager,
                        user_id=user_id,
                        vnc_mode=True,  # ENABLE VNC!
                        vnc_port=5900 + len(vnc_session_manager.sessions),  # Auto-assign port
                        resume_path=resume_path  # Pass resume path
                    )
                )
                
                logger.info(f"✅ Agent completed for session {session_id}")
                logger.info(f"   VNC info: {vnc_info}")
                
            except Exception as e:
                logger.error(f"Error in agent thread: {e}")
            finally:
                loop.close()
        
        # Start agent in background thread
        thread = threading.Thread(target=run_agent_async, daemon=True)
        thread.start()
        
        # Wait a bit for VNC to initialize
        import time
        time.sleep(3)

        # Calculate ports (VNC and WebSocket)
        vnc_port = 5900 + len(vnc_session_manager.sessions)
        ws_port = 6900 + (vnc_port - 5900)  # Offset from base

        # Register session for WebSocket proxying
        register_vnc_session(session_id, vnc_port, ws_port)
        logger.info(f"📝 Registered session {session_id} - VNC:{vnc_port}, WS:{ws_port}")

        # Return VNC connection info immediately
        # (Agent will keep browser alive in background)

        # Determine WebSocket protocol and URL based on environment
        import os
        is_development = os.getenv('FLASK_ENV') == 'development' or 'localhost' in request.host
        ws_protocol = 'ws' if (is_development and 'localhost' in request.host) else 'wss'

        # Use Flask WebSocket route for VNC streaming
        # This proxies to the local websockify instance
        websocket_url = f"{ws_protocol}://{request.host}/vnc-stream/{session_id}"

        logger.info(f"📡 WebSocket URL: {websocket_url}")
        logger.info(f"   Session ID: {session_id}")
        logger.info(f"   Is development: {is_development}")

        return jsonify({
            "success": True,
            "session_id": session_id,
            "vnc_port": vnc_port,
            "websocket_url": websocket_url,
            "websocket_port": ws_port,
            "message": "VNC session started - connecting...",
            "instructions": "Browser is being filled by agent. You can watch and take over when ready.",
            "is_development": is_development
        }), 200
        
    except Exception as e:
        logger.error(f"Error starting VNC job application: {e}")
        return jsonify({"error": str(e)}), 500


@vnc_api.route("/api/vnc/sessions", methods=['GET'])
@require_auth
def get_vnc_sessions():
    """
    Get all active VNC sessions for current user
    
    Response:
    {
        "sessions": [
            {
                "session_id": "uuid",
                "job_url": "https://...",
                "vnc_port": 5900,
                "status": "active",
                "created_at": "2025-01-18T10:30:00"
            }
        ]
    }
    """
    try:
        user_id = request.current_user['id']
        sessions = vnc_session_manager.get_user_sessions(user_id)
        
        return jsonify({
            "success": True,
            "sessions": sessions
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting VNC sessions: {e}")
        return jsonify({"error": str(e)}), 500


@vnc_api.route("/api/vnc/session/<session_id>", methods=['GET'])
@require_auth
def get_vnc_session(session_id):
    """
    Get specific VNC session info
    
    Response:
    {
        "session_id": "uuid",
        "job_url": "https://...",
        "vnc_port": 5900,
        "websocket_url": "wss://...",
        "status": "active"
    }
    """
    try:
        logger.info(f"🔍 Looking for VNC session: {session_id}")
        
        # Try VNC session manager first
        session = vnc_session_manager.get_session(session_id)
        logger.info(f"   VNC manager: {'Found' if session else 'Not found'}")
        
        # Fall back to dev session manager (for Windows local development)
        if not session:
            session = dev_browser_session.get_session(session_id)
            logger.info(f"   Dev manager: {'Found' if session else 'Not found'}")
        
        # Debug: Log all available sessions
        if not session:
            logger.warning(f"❌ Session {session_id} not found anywhere")
            logger.info(f"   Available VNC sessions: {list(vnc_session_manager.sessions.keys())}")
            logger.info(f"   Available Dev sessions: {list(dev_browser_session.sessions.keys())}")
            return jsonify({"error": "Session not found"}), 404
        
        # Verify user owns this session
        user_id = request.current_user['id']
        if session.get('user_id') != user_id:
            return jsonify({"error": "Unauthorized"}), 403
        
        # Check if this is a dev session (Windows local development)
        is_dev_session = session.get('dev_mode', False)
        
        if is_dev_session:
            # Development mode - VNC not available
            # Browser is open locally but no VNC stream
            logger.info(f"📝 Returning dev session info for {session_id}")
            return jsonify({
                "success": True,
                "session_id": session_id,
                "job_url": session.get('job_url'),
                "vnc_port": None,
                "websocket_url": None,  # No WebSocket in dev mode
                "status": session.get('status'),
                "created_at": None,
                "is_development": True,
                "dev_mode": True,
                "message": "⚠️ VNC not available on Windows. Browser is open locally - check your screen! Deploy to Railway for VNC streaming."
            }), 200
        
        # Real VNC session
        # Determine WebSocket URL based on environment
        import os
        is_development = os.getenv('FLASK_ENV') == 'development' or 'localhost' in request.host
        ws_protocol = 'ws' if is_development else 'wss'
        
        if is_development:
            websocket_url = f"{ws_protocol}://localhost:6900"
        else:
            websocket_url = f"{ws_protocol}://{request.host}/vnc-stream/{session_id}"
        
        return jsonify({
            "success": True,
            "session_id": session_id,
            "job_url": session.get('job_url'),
            "vnc_port": session.get('vnc_port'),
            "websocket_url": websocket_url,
            "websocket_port": 6900,
            "status": session.get('status'),
            "created_at": session.get('created_at'),
            "is_development": is_development,
            "dev_mode": False
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting VNC session: {e}")
        return jsonify({"error": str(e)}), 500


@vnc_api.route("/api/vnc/session/<session_id>", methods=['DELETE'])
@require_auth
def close_vnc_session(session_id):
    """
    Close a VNC session and cleanup resources
    
    Response:
    {
        "success": true,
        "message": "Session closed"
    }
    """
    try:
        session = vnc_session_manager.get_session(session_id)
        
        if not session:
            return jsonify({"error": "Session not found"}), 404
        
        # Verify user owns this session
        user_id = request.current_user['id']
        if session.get('user_id') != user_id:
            return jsonify({"error": "Unauthorized"}), 403
        
        # Close session asynchronously
        loop = asyncio.new_event_loop()
        success = loop.run_until_complete(
            vnc_session_manager.close_session(session_id)
        )
        loop.close()
        
        if success:
            return jsonify({
                "success": True,
                "message": "VNC session closed"
            }), 200
        else:
            return jsonify({"error": "Failed to close session"}), 500
            
    except Exception as e:
        logger.error(f"Error closing VNC session: {e}")
        return jsonify({"error": str(e)}), 500


@vnc_api.route("/api/vnc/health", methods=['GET'])
def vnc_health():
    """
    Health check for VNC infrastructure
    
    Response:
    {
        "status": "healthy",
        "active_sessions": 3,
        "available_ports": 7,
        "vnc_available": true
    }
    """
    try:
        active_count = len(vnc_session_manager.sessions)
        available_ports = vnc_session_manager.max_sessions - active_count
        
        return jsonify({
            "status": "healthy",
            "active_sessions": active_count,
            "available_ports": available_ports,
            "max_sessions": vnc_session_manager.max_sessions,
            "vnc_available": True
        }), 200
        
    except Exception as e:
        logger.error(f"Error in VNC health check: {e}")
        return jsonify({
            "status": "unhealthy",
            "error": str(e),
            "vnc_available": False
        }), 500


# ============= BATCH VNC ENDPOINTS (NEW) =============

@vnc_api.route("/api/vnc/batch-apply", methods=['POST'])
@require_auth
def batch_apply_with_vnc():
    """
    Start batch job application with VNC sessions
    Processes jobs sequentially, each gets own VNC session
    
    Request:
    {
        "jobUrls": ["url1", "url2", "url3"]
    }
    
    Response:
    {
        "success": true,
        "batch_id": "batch-uuid",
        "total_jobs": 3,
        "jobs": [...]
    }
    """

    
    try:
        data = request.json
        job_urls = data.get('jobUrls', [])
        user_id = request.current_user['id']
        
        if not job_urls or not isinstance(job_urls, list):
            return jsonify({"error": "jobUrls must be a non-empty list"}), 400
        
        if len(job_urls) > 10:
            return jsonify({"error": "Maximum 10 jobs per batch"}), 400
        
        logger.info(f"📦 Starting batch VNC apply for user {user_id}")
        logger.info(f"   Jobs: {len(job_urls)}")
        
        # Get resume from profile (batch mode doesn't take resumeUrl)
        resume_url = None
        try:
            profile = ProfileService.get_profile(user_id)
            if profile:
                resume_url = profile.get('resume_url')
        except Exception as e:
            logger.warning(f"Could not fetch profile for user {user_id}: {e}")
            
        # Download resume to temp file
        resume_path = _download_resume_to_temp(resume_url)
        logger.info(f"   Resume: {resume_path if resume_path else 'None'}")
        
        # Create batch
        batch_id = batch_vnc_manager.create_batch(user_id, job_urls)
        batch = batch_vnc_manager.get_batch(batch_id)
        
        # Capture request host for WebSocket URL generation (before thread)
        request_host = request.host
        is_development = os.getenv('FLASK_ENV') == 'development' or 'localhost' in request_host
        
        # Start processing in background thread
        import threading
        
        def process_batch_sequential():
            """Process all jobs in the batch sequentially"""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                for idx, job in enumerate(batch.jobs):
                    try:
                        logger.info("=" * 80)
                        logger.info(f"🎯 Processing job {idx + 1}/{len(batch.jobs)}")
                        logger.info(f"   Job ID: {job.job_id}")
                        logger.info(f"   Job URL: {job.job_url}")
                        logger.info(f"   Batch ID: {batch_id}")
                        logger.info("=" * 80)

                        # Update status: filling
                        batch_vnc_manager.update_job_status(
                            batch_id, job.job_id, 'filling', progress=0
                        )

                        # Calculate VNC port (5900, 5901, 5902, etc.)
                        # Each job gets its own port for isolation
                        vnc_port = 5900 + idx
                        logger.info(f"📡 Allocated VNC port {vnc_port} for job {idx + 1}")
                        
                        # Run agent with VNC mode (agent creates VNC internally)
                        vnc_info = loop.run_until_complete(
                            run_links_with_refactored_agent(
                                links=[job.job_url],
                                headless=False,  # Visible on virtual display
                                keep_open=True,  # Keep browser open!
                                debug=False,
                                hold_seconds=0,
                                slow_mo_ms=100,  # Slight slow-mo for visibility
                                job_id=job.job_id,
                                jobs_dict={},
                                session_manager=session_manager,
                                user_id=user_id,
                                vnc_mode=True,  # ENABLE VNC!
                                vnc_port=vnc_port,
                                resume_path=resume_path  # Pass resume path
                            )
                        )
                        
                        # VNC info might be None if agent went through human intervention
                        # Register session info either way so API can find it
                        # Ensure VNC actually started; otherwise mark error and continue
                        if not vnc_info or not vnc_info.get('vnc_enabled'):
                            logger.error(f"❌ VNC failed to start for job {job.job_id}")
                            batch_vnc_manager.update_job_status(
                                batch_id, job.job_id, 'error',
                                error="VNC environment failed to start"
                            )
                            continue  # Skip to next job

                        vnc_session_id = job.job_id  # Use job_id as session_id
                        actual_vnc_port = vnc_port

                        # Verify VNC session was registered by agent
                        if vnc_info and vnc_info.get('vnc_enabled'):
                            logger.info(f"✅ VNC mode active for job {job.job_id}")
                        else:
                            logger.warning(f"⚠️ VNC info not returned, but browser should be open")

                        # Agent already registered the session in vnc_session_manager with coordinator reference
                        # We just need to register for WebSocket proxy routing
                        try:
                            from Agents.components.vnc import vnc_session_manager as vsm

                            # Verify session was registered by agent
                            if vnc_session_id in vsm.sessions:
                                logger.info(f"✅ Session {vnc_session_id} already registered by agent with coordinator")
                            else:
                                logger.warning(f"⚠️ Session {vnc_session_id} NOT found in manager - agent may have failed")
                                # Fallback registration (without coordinator)
                                vsm.sessions[vnc_session_id] = {
                                    'session_id': vnc_session_id,
                                    'user_id': user_id,
                                    'job_url': job.job_url,
                                    'vnc_port': actual_vnc_port,
                                    'status': 'active',
                                    'created_at': datetime.now()
                                }

                            # CRITICAL: Register in vnc_stream_proxy for WebSocket routing
                            ws_port = 6900 + idx  # Calculate websockify port
                            register_vnc_session(vnc_session_id, actual_vnc_port, ws_port)
                            logger.info(f"📝 Registered session {vnc_session_id} for WebSocket proxy - VNC:{actual_vnc_port}, WS:{ws_port}")

                        except Exception as e:
                            logger.error(f"❌ Failed to verify/register VNC session: {e}")

                            # Fallback: Register in dev session manager
                            dev_browser_session.register_session(
                                session_id=job.job_id,
                                job_url=job.job_url,
                                user_id=user_id,
                                current_url=job.job_url
                            )
                            logger.info(f"📝 Registered as dev session: {job.job_id}")
                        
                        # Determine WebSocket URL
                        ws_protocol = 'ws' if is_development else 'wss'
                        
                        if is_development:
                            vnc_url = f"{ws_protocol}://localhost:{6900 + idx}"
                        else:
                            vnc_url = f"{ws_protocol}://{request_host}/vnc-stream/{vnc_session_id}"
                        
                        # Update status based on agent result
                        # If session has status 'intervention', we map it to 'ready_for_review' 
                        # so the user sees the "Open Browser" option.
                        
                        final_status = 'ready_for_review' # Default success state
                        
                        # Check if session is in intervention mode
                        try:
                            from Agents.components.vnc import vnc_session_manager as vsm
                            if vnc_session_id in vsm.sessions:
                                session = vsm.sessions[vnc_session_id]
                                if session.get('status') == 'intervention':
                                    logger.info(f"⚠️ Job {job.job_id} requires intervention - marking ready for review")
                                    # We keep it as ready_for_review for the frontend to show the button,
                                    # but we might want to flag it differently in future.
                        except:
                            pass

                        # Update status: ready for review
                        batch_vnc_manager.update_job_status(
                            batch_id, job.job_id, final_status,
                            progress=100,
                            vnc_session_id=vnc_session_id,
                            vnc_port=actual_vnc_port,
                            vnc_url=vnc_url
                        )
                        
                        logger.info(f"✅ Job {idx + 1} ready for review: {job.job_url}")
                        
                    except Exception as e:
                        logger.error(f"❌ Job {idx + 1} failed: {e}")
                        batch_vnc_manager.update_job_status(
                            batch_id, job.job_id, 'failed',
                            error=str(e)
                        )
                
                # Mark batch as completed
                batch.status = 'completed'
                logger.info(f"✅ Batch {batch_id} processing completed")
                
            except Exception as e:
                logger.error(f"Error processing batch {batch_id}: {e}")
                batch.status = 'failed'
            finally:
                loop.close()
        
        # Start background processing
        thread = threading.Thread(target=process_batch_sequential, daemon=True)
        thread.start()
        
        # Return initial batch status immediately
        return jsonify({
            "success": True,
            "batch_id": batch_id,
            "total_jobs": len(job_urls),
            "jobs": batch.to_dict()['jobs'],
            "message": f"Started processing {len(job_urls)} jobs sequentially"
        }), 200
        
    except Exception as e:
        logger.error(f"Error starting batch VNC apply: {e}")
        return jsonify({"error": str(e)}), 500


@vnc_api.route("/api/vnc/batch/<batch_id>/status", methods=['GET'])
@require_auth
def get_batch_status(batch_id):
    """
    Get current status of batch and all jobs
    Frontend polls this every 2 seconds for updates
    
    Response:
    {
        "batch_id": "uuid",
        "status": "processing|completed",
        "total_jobs": 5,
        "completed_jobs": 2,
        "ready_for_review": 1,
        "filling_jobs": 1,
        "jobs": [...]
    }
    """
    try:
        batch = batch_vnc_manager.get_batch(batch_id)
        
        if not batch:
            return jsonify({"error": "Batch not found"}), 404
        
        # Verify user owns this batch
        user_id = request.current_user['id']
        if batch.user_id != user_id:
            return jsonify({"error": "Unauthorized"}), 403
        
        return jsonify(batch.to_dict()), 200
        
    except Exception as e:
        logger.error(f"Error getting batch status: {e}")
        return jsonify({"error": str(e)}), 500


@vnc_api.route("/api/vnc/batch/<batch_id>/job/<job_id>/submit", methods=['POST'])
@require_auth
def mark_job_submitted(batch_id, job_id):
    """
    Mark a job as submitted by user
    Called after user manually submits the application
    
    Response:
    {
        "success": true,
        "message": "Job marked as submitted"
    }
    """
    try:
        batch = batch_vnc_manager.get_batch(batch_id)
        
        if not batch:
            return jsonify({"error": "Batch not found"}), 404
        
        # Verify user owns this batch
        user_id = request.current_user['id']
        if batch.user_id != user_id:
            return jsonify({"error": "Unauthorized"}), 403
        
        # Mark job as completed
        success = batch_vnc_manager.mark_job_submitted(batch_id, job_id)
        
        if success:
            logger.info(f"✅ Job {job_id} marked as submitted by user")
            
            # Check if batch is complete
            if batch_vnc_manager.is_batch_complete(batch_id):
                logger.info(f"🎉 Batch {batch_id} fully completed!")
            
            return jsonify({
                "success": True,
                "message": "Job marked as submitted"
            }), 200
        else:
            return jsonify({"error": "Job not found"}), 404
            
    except Exception as e:
        logger.error(f"Error marking job submitted: {e}")
        return jsonify({"error": str(e)}), 500


@vnc_api.route("/api/vnc/batch-apply-with-preferences", methods=['POST'])
@require_auth
def batch_apply_with_preferences():
    """
    Start batch job application with resume tailoring preferences

    Request:
    {
        "jobs": [
            {"url": "job_url_1", "tailorResume": true},
            {"url": "job_url_2", "tailorResume": false},
            ...
        ]
    }

    Response:
    {
        "success": true,
        "batch_id": "batch-uuid",
        "total_jobs": 3,
        "jobs": [...]
    }
    """
    try:
        data = request.json
        jobs_data = data.get('jobs', [])
        user_id = request.current_user['id']

        if not jobs_data or not isinstance(jobs_data, list):
            return jsonify({"error": "jobs must be a non-empty list"}), 400

        if len(jobs_data) > 10:
            return jsonify({"error": "Maximum 10 jobs per batch"}), 400

        # Extract URLs and preferences
        job_urls = [job['url'] for job in jobs_data]
        tailor_preferences = {job['url']: job.get('tailorResume', False) for job in jobs_data}

        logger.info(f"📦 Starting batch VNC apply with preferences for user {user_id}")
        logger.info(f"   Jobs: {len(job_urls)}")
        logger.info(f"   Tailoring: {sum(tailor_preferences.values())} jobs")

        # Get resume from profile (batch mode doesn't take resumeUrl)
        resume_url = None
        try:
            profile = ProfileService.get_profile(user_id)
            if profile:
                resume_url = profile.get('resume_url')
        except Exception as e:
            logger.warning(f"Could not fetch profile for user {user_id}: {e}")
            
        # Download resume to temp file
        resume_path = _download_resume_to_temp(resume_url)
        logger.info(f"   Resume: {resume_path if resume_path else 'None'}")

        # Create batch
        batch_id = batch_vnc_manager.create_batch(user_id, job_urls)
        batch = batch_vnc_manager.get_batch(batch_id)

        # Store tailoring preferences in batch
        batch.tailor_preferences = tailor_preferences

        # Capture request host for WebSocket URL generation (before thread)
        request_host = request.host
        is_development = os.getenv('FLASK_ENV') == 'development' or 'localhost' in request_host

        # Start processing in background thread
        import threading

        def process_batch_sequential():
            """Process all jobs in the batch sequentially"""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                for idx, job in enumerate(batch.jobs):
                    try:
                        logger.info(f"🎯 Processing job {idx + 1}/{len(batch.jobs)}: {job.job_url}")

                        # Check if this job should have resume tailored
                        should_tailor = tailor_preferences.get(job.job_url, False)
                        logger.info(f"   Resume tailoring: {'Yes' if should_tailor else 'No'}")

                        # Update status: filling
                        batch_vnc_manager.update_job_status(
                            batch_id, job.job_id, 'filling', progress=0
                        )

                        # Calculate VNC port (5900, 5901, 5902, etc.)
                        vnc_port = 5900 + idx

                        # Run agent with VNC mode (agent creates VNC internally)
                        # TODO: Pass should_tailor to agent when resume tailoring is implemented
                        vnc_info = loop.run_until_complete(
                            run_links_with_refactored_agent(
                                links=[job.job_url],
                                headless=False,  # Visible on virtual display
                                keep_open=True,  # Keep browser open!
                                debug=False,
                                hold_seconds=0,
                                slow_mo_ms=100,  # Slight slow-mo for visibility
                                job_id=job.job_id,
                                jobs_dict={},
                                session_manager=session_manager,
                                user_id=user_id,
                                vnc_mode=True,  # ENABLE VNC!
                                vnc_port=vnc_port,
                                tailor_resume=should_tailor,  # Pass tailoring preference
                                resume_path=resume_path  # Pass resume path
                            )
                        )

                        vnc_session_id = job.job_id  # Use job_id as session_id
                        actual_vnc_port = vnc_port

                        # CRITICAL: Register session in vnc_session_manager so the API can find it
                        try:
                            from Agents.components.vnc import vnc_session_manager as vsm

                            # Preserve existing status if intervention already marked it
                            existing_entry = vsm.sessions.get(vnc_session_id, {})
                            session_status = existing_entry.get('status', 'active')

                            vsm.sessions[vnc_session_id] = {
                                'session_id': vnc_session_id,
                                'user_id': user_id,
                                'job_url': job.job_url,
                                'vnc_port': actual_vnc_port,
                                'status': session_status,
                                'created_at': datetime.now()
                            }

                            logger.info(f"✅ Registered VNC session {vnc_session_id} in global manager (status: {session_status})")

                            # CRITICAL: Also register in vnc_stream_proxy for WebSocket routing
                            ws_port = 6900 + idx  # Calculate websockify port
                            register_vnc_session(vnc_session_id, actual_vnc_port, ws_port)
                            logger.info(f"📝 Registered session {vnc_session_id} for WebSocket proxy - VNC:{actual_vnc_port}, WS:{ws_port}")

                        except Exception as e:
                            logger.warning(f"Could not register in VNC manager: {e}")

                            # Fallback: Register in dev session manager so at least frontend knows about it
                            dev_browser_session.register_session(
                                session_id=job.job_id,
                                job_url=job.job_url,
                                user_id=user_id,
                                current_url=job.job_url
                            )
                            logger.info(f"📝 Registered as dev session: {job.job_id}")

                        # Determine WebSocket URL
                        ws_protocol = 'ws' if is_development else 'wss'

                        if is_development:
                            vnc_url = f"{ws_protocol}://localhost:{6900 + idx}"
                        else:
                            vnc_url = f"{ws_protocol}://{request_host}/vnc-stream/{vnc_session_id}"

                        # Determine final status (map intervention to ready_for_review so UI shows button)
                        final_status = 'ready_for_review'
                        try:
                            from Agents.components.vnc import vnc_session_manager as vsm
                            session_entry = vsm.sessions.get(vnc_session_id)
                            if session_entry and session_entry.get('status') == 'intervention':
                                logger.info(f"⚠️ Job {job.job_id} requires intervention - marking ready for review for manual takeover")
                        except Exception:
                            pass

                        # Update status: ready for review
                        batch_vnc_manager.update_job_status(
                            batch_id, job.job_id, final_status,
                            progress=100,
                            vnc_session_id=vnc_session_id,
                            vnc_port=actual_vnc_port,
                            vnc_url=vnc_url
                        )

                        logger.info(f"✅ Job {idx + 1} ready for review: {job.job_url}")

                    except Exception as e:
                        logger.error(f"❌ Job {idx + 1} failed: {e}")
                        batch_vnc_manager.update_job_status(
                            batch_id, job.job_id, 'failed',
                            error=str(e)
                        )

                # Mark batch as completed
                batch.status = 'completed'
                logger.info(f"✅ Batch {batch_id} processing completed")

            except Exception as e:
                logger.error(f"Error processing batch {batch_id}: {e}")
                batch.status = 'failed'
            finally:
                loop.close()

        # Start background processing
        thread = threading.Thread(target=process_batch_sequential, daemon=True)
        thread.start()

        # Return initial batch status immediately
        return jsonify({
            "success": True,
            "batch_id": batch_id,
            "total_jobs": len(job_urls),
            "jobs": batch.to_dict()['jobs'],
            "message": f"Started processing {len(job_urls)} jobs sequentially"
        }), 200

    except Exception as e:
        logger.error(f"Error starting batch VNC apply with preferences: {e}")
        return jsonify({"error": str(e)}), 500


@vnc_api.route("/api/vnc/batch/<batch_id>", methods=['DELETE'])
@require_auth
def delete_batch(batch_id):
    """
    Delete a batch and close all associated VNC sessions
    
    Response:
    {
        "success": true,
        "message": "Batch and all sessions closed"
    }
    """
    try:
        batch = batch_vnc_manager.get_batch(batch_id)
        
        if not batch:
            return jsonify({"error": "Batch not found"}), 404
        
        # Verify user owns this batch
        user_id = request.current_user['id']
        if batch.user_id != user_id:
            return jsonify({"error": "Unauthorized"}), 403
        
        # Close all VNC sessions for this batch
        loop = asyncio.new_event_loop()
        for job in batch.jobs:
            if job.vnc_session_id:
                try:
                    loop.run_until_complete(
                        vnc_session_manager.close_session(job.vnc_session_id)
                    )
                except Exception as e:
                    logger.warning(f"Failed to close VNC session {job.vnc_session_id}: {e}")
        loop.close()
        
        # Remove batch
        if batch_id in batch_vnc_manager.batches:
            del batch_vnc_manager.batches[batch_id]
        
        logger.info(f"✅ Batch {batch_id} deleted")
        
        return jsonify({
            "success": True,
            "message": "Batch and all VNC sessions closed"
        }), 200

    except Exception as e:
        logger.error(f"Error deleting batch: {e}")
        return jsonify({"error": str(e)}), 500


@vnc_api.route("/api/vnc/dashboard", methods=['GET'])
@require_auth
def get_vnc_dashboard():
    """
    Get unified dashboard data with all user's batch jobs

    Returns all jobs from all batches in a flat list for easy filtering/sorting

    Response:
    {
        "success": true,
        "total_applications": 25,
        "statistics": {
            "queued": 5,
            "filling": 2,
            "ready_for_review": 3,
            "completed": 10,
            "failed": 5
        },
        "applications": [
            {
                "job_id": "uuid",
                "batch_id": "uuid",
                "job_url": "https://...",
                "status": "queued|filling|ready_for_review|completed|failed",
                "progress": 0-100,
                "vnc_session_id": "uuid",
                "vnc_url": "wss://...",
                "error": "error message if any",
                "started_at": "ISO datetime",
                "completed_at": "ISO datetime",
                "submitted_by_user_at": "ISO datetime"
            }
        ],
        "batches": [
            {
                "batch_id": "uuid",
                "created_at": "ISO datetime",
                "status": "processing|completed",
                "total_jobs": 5,
                "completed_jobs": 2
            }
        ]
    }
    """
    try:
        user_id = request.current_user['id']

        # Get all batches for this user
        user_batches = batch_vnc_manager.get_user_batches(user_id)

        # Collect all jobs from all batches
        all_applications = []
        statistics = {
            'queued': 0,
            'filling': 0,
            'ready_for_review': 0,
            'completed': 0,
            'failed': 0
        }

        batch_summaries = []

        for batch in user_batches:
            # Add batch summary
            batch_summaries.append({
                'batch_id': batch.batch_id,
                'created_at': batch.created_at.isoformat(),
                'status': batch.status,
                'total_jobs': len(batch.jobs),
                'completed_jobs': sum(1 for job in batch.jobs if job.status == 'completed'),
                'ready_for_review': sum(1 for job in batch.jobs if job.status == 'ready_for_review'),
                'filling_jobs': sum(1 for job in batch.jobs if job.status == 'filling'),
                'failed_jobs': sum(1 for job in batch.jobs if job.status == 'failed')
            })

            # Collect all jobs
            for job in batch.jobs:
                all_applications.append(job.to_dict())

                # Update statistics
                if job.status in statistics:
                    statistics[job.status] += 1

        # Sort applications by created date (most recent first)
        all_applications.sort(
            key=lambda x: x.get('started_at') or x.get('job_id'),
            reverse=True
        )

        return jsonify({
            'success': True,
            'total_applications': len(all_applications),
            'statistics': statistics,
            'applications': all_applications,
            'batches': batch_summaries
        }), 200

    except Exception as e:
        logger.error(f"Error getting VNC dashboard: {e}")
        return jsonify({"error": str(e)}), 500

