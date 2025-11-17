"""
Job Handlers for Production Job Queue System
Implements handlers for resume tailoring, job applications, and job search
"""

import logging
import time
import os
import sys
from typing import Dict, Any, Optional
from datetime import datetime

# Add paths for imports
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'Agents'))

from job_queue import job_handler, JobPriority
from rate_limiter import rate_limiter, with_gemini_quota
from security_manager import security_manager
from database_optimizer import get_optimized_db_session

# Import existing agents
from resume_tailoring_agent import tailor_resume_and_return_url
from job_application_agent import run_links_with_refactored_agent
from multi_source_job_discovery_agent import MultiSourceJobDiscoveryAgent

logger = logging.getLogger(__name__)

@job_handler("resume_tailoring")
def handle_resume_tailoring(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle resume tailoring job with proper resource management
    """
    start_time = time.time()
    user_id = payload.get('user_id')
    
    try:
        logger.info(f"Starting resume tailoring job for user {user_id}")
        
        # Extract parameters
        original_resume_url = payload.get('original_resume_url')
        job_description = payload.get('job_description')
        job_title = payload.get('job_title', 'Unknown Position')
        company = payload.get('company', 'Unknown Company')
        credentials_dict = payload.get('credentials')  # Google OAuth credentials dictionary
        user_full_name = payload.get('user_full_name', 'User')

        # Reconstruct Credentials object from dictionary
        from google.oauth2.credentials import Credentials
        credentials = None
        if credentials_dict:
            credentials = Credentials(
                token=credentials_dict.get('token'),
                refresh_token=credentials_dict.get('refresh_token'),
                token_uri=credentials_dict.get('token_uri'),
                client_id=credentials_dict.get('client_id'),
                client_secret=credentials_dict.get('client_secret'),
                scopes=credentials_dict.get('scopes')
            )
        
        # Validate required parameters
        if not original_resume_url or not job_description:
            raise ValueError("original_resume_url and job_description are required")
        
        # Check user rate limits
        allowed, info = rate_limiter.check_limit('resume_tailoring_per_user_per_day', str(user_id))
        if not allowed:
            raise Exception(f"Daily resume tailoring limit exceeded. Try again in {info.get('retry_after', 3600)} seconds.")
        
        # Check Gemini quota
        from rate_limiter import gemini_quota_manager
        can_request, quota_info = gemini_quota_manager.can_make_request()
        if not can_request:
            raise Exception(f"API quota exceeded: {quota_info.get('reason')}. Try again later.")
        
        # Reserve Gemini quota
        reservation_id = gemini_quota_manager.reserve_quota(user_id, JobPriority.NORMAL.value)
        
        try:
            # Get user's Mimikree credentials
            from mimikree_service import mimikree_service
            mimikree_credentials = mimikree_service.get_user_mimikree_credentials(user_id)
            
            mimikree_email = None
            mimikree_password = None
            
            if mimikree_credentials:
                mimikree_email, mimikree_password = mimikree_credentials
                logger.info(f"Using user's Mimikree credentials for tailoring")
            else:
                logger.warning(f"User {user_id} has no Mimikree credentials - tailoring will use limited features")
                # Continue without Mimikree - the tailoring agent can handle this
            
            # Execute resume tailoring
            tailoring_result = tailor_resume_and_return_url(
                original_resume_url=original_resume_url,
                job_description=job_description,
                job_title=job_title,
                company=company,
                credentials=credentials,
                mimikree_email=mimikree_email,
                mimikree_password=mimikree_password,
                user_full_name=user_full_name
            )

            # Increment global Gemini usage counters
            # Note: User-specific resume_tailoring counter was already incremented by check_limit()
            rate_limiter.increment_usage('gemini_requests_per_minute', 'global')
            rate_limiter.increment_usage('gemini_requests_per_day', 'global')
            
            # Log successful tailoring
            security_manager.log_security_event(
                event_type=security_manager.SECURITY_EVENTS['DATA_ACCESS'],
                user_id=user_id,
                details={
                    'action': 'resume_tailoring',
                    'job_title': job_title,
                    'company': company,
                    'duration_seconds': time.time() - start_time
                }
            )
            
            execution_time = time.time() - start_time
            logger.info(f"Resume tailoring completed for user {user_id} in {execution_time:.2f}s")
            
            return {
                'success': True,
                'tailoring_result': tailoring_result,
                'execution_time': execution_time,
                'job_title': job_title,
                'company': company
            }
            
        finally:
            # Always release quota
            gemini_quota_manager.release_quota(reservation_id)
    
    except Exception as e:
        error_msg = f"Resume tailoring failed for user {user_id}: {str(e)}"
        logger.error(error_msg)
        
        # Log security event for failures
        security_manager.log_security_event(
            event_type=security_manager.SECURITY_EVENTS['API_ABUSE'],
            user_id=user_id,
            details={
                'action': 'resume_tailoring_failed',
                'error': str(e),
                'duration_seconds': time.time() - start_time
            }
        )
        
        return {
            'success': False,
            'error': error_msg,
            'execution_time': time.time() - start_time
        }

@job_handler("job_application")
def handle_job_application(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle job application automation with browser session management
    """
    start_time = time.time()
    user_id = payload.get('user_id')
    
    try:
        logger.info(f"Starting job application for user {user_id}")
        
        # Extract parameters
        job_url = payload.get('job_url')
        resume_url = payload.get('resume_url')
        use_tailored = payload.get('use_tailored', False)
        tailored_resume_url = payload.get('tailored_resume_url')
        
        # Validate required parameters
        if not job_url or not resume_url:
            raise ValueError("job_url and resume_url are required")
        
        # Check user rate limits
        allowed, info = rate_limiter.check_limit('job_applications_per_user_per_day', str(user_id))
        if not allowed:
            raise Exception(f"Daily job application limit exceeded. Try again in {info.get('retry_after', 3600)} seconds.")
        
        # Check concurrent session limits
        allowed, info = rate_limiter.check_limit('concurrent_job_applications', str(user_id))
        if not allowed:
            raise Exception("Maximum concurrent job applications reached. Please wait for current applications to complete.")
        
        # Determine which resume to use
        final_resume_url = tailored_resume_url if use_tailored and tailored_resume_url else resume_url
        
        # Import session manager
        from components.session.session_manager import SessionManager
        session_storage_path = os.path.join(os.path.dirname(__file__), "sessions")
        session_manager = SessionManager(session_storage_path)
        
        # Execute job application
        import asyncio
        
        async def run_application():
            return await run_links_with_refactored_agent(
                links=[job_url],
                headless=True,
                keep_open=False,
                debug=False,
                hold_seconds=2,
                slow_mo_ms=0,
                job_id=f"job_queue_{user_id}_{int(time.time())}",
                jobs_dict={},  # Empty dict for job queue context
                session_manager=session_manager
            )
        
        # Run the async job application
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            result = loop.run_until_complete(run_application())
        finally:
            loop.close()
        
        # Increment usage counter
        rate_limiter.increment_usage('job_applications_per_user_per_day', str(user_id))
        
        # Log successful application
        security_manager.log_security_event(
            event_type=security_manager.SECURITY_EVENTS['DATA_ACCESS'],
            user_id=user_id,
            details={
                'action': 'job_application',
                'job_url': job_url,
                'use_tailored': use_tailored,
                'duration_seconds': time.time() - start_time
            }
        )
        
        execution_time = time.time() - start_time
        logger.info(f"Job application completed for user {user_id} in {execution_time:.2f}s")
        
        return {
            'success': True,
            'job_url': job_url,
            'result': result,
            'execution_time': execution_time
        }
    
    except Exception as e:
        error_msg = f"Job application failed for user {user_id}: {str(e)}"
        logger.error(error_msg)
        
        # Log security event for failures
        security_manager.log_security_event(
            event_type=security_manager.SECURITY_EVENTS['API_ABUSE'],
            user_id=user_id,
            details={
                'action': 'job_application_failed',
                'error': str(e),
                'duration_seconds': time.time() - start_time
            }
        )
        
        return {
            'success': False,
            'error': error_msg,
            'execution_time': time.time() - start_time
        }

@job_handler("job_search")
def handle_job_search(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle job search across multiple sources
    """
    start_time = time.time()
    user_id = payload.get('user_id')
    
    try:
        logger.info(f"Starting job search for user {user_id}")
        
        # Extract parameters
        min_relevance_score = payload.get('min_relevance_score', 30)
        
        # Check user rate limits
        allowed, info = rate_limiter.check_limit('job_search_per_user_per_day', str(user_id))
        if not allowed:
            raise Exception(f"Daily job search limit exceeded. Try again in {info.get('retry_after', 3600)} seconds.")
        
        # Initialize job discovery agent
        job_discovery_agent = MultiSourceJobDiscoveryAgent(user_id=user_id)
        
        if not job_discovery_agent.profile_data:
            raise Exception("Profile data not found for this user")
        
        # Execute job search
        response = job_discovery_agent.search_and_save(min_relevance_score=min_relevance_score)
        
        if 'error' in response:
            raise Exception(response['error'])
        
        # Increment usage counter
        rate_limiter.increment_usage('job_search_per_user_per_day', str(user_id))
        
        # Log successful search
        security_manager.log_security_event(
            event_type=security_manager.SECURITY_EVENTS['DATA_ACCESS'],
            user_id=user_id,
            details={
                'action': 'job_search',
                'min_relevance_score': min_relevance_score,
                'jobs_found': response.get('count', 0),
                'duration_seconds': time.time() - start_time
            }
        )
        
        execution_time = time.time() - start_time
        logger.info(f"Job search completed for user {user_id} in {execution_time:.2f}s - found {response.get('count', 0)} jobs")
        
        return {
            'success': True,
            'jobs': response.get('jobs', []),
            'total_found': response.get('count', 0),
            'sources': response.get('sources', {}),
            'average_score': response.get('average_score', 0),
            'saved_count': response.get('saved_count', 0),
            'updated_count': response.get('updated_count', 0),
            'execution_time': execution_time
        }
    
    except Exception as e:
        error_msg = f"Job search failed for user {user_id}: {str(e)}"
        logger.error(error_msg)
        
        # Log security event for failures
        security_manager.log_security_event(
            event_type=security_manager.SECURITY_EVENTS['API_ABUSE'],
            user_id=user_id,
            details={
                'action': 'job_search_failed',
                'error': str(e),
                'duration_seconds': time.time() - start_time
            }
        )
        
        return {
            'success': False,
            'error': error_msg,
            'execution_time': time.time() - start_time
        }

@job_handler("project_analysis")
def handle_project_analysis(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle project relevance analysis for job tailoring
    """
    start_time = time.time()
    user_id = payload.get('user_id')
    
    try:
        logger.info(f"Starting project analysis for user {user_id}")
        
        # Extract parameters
        job_description = payload.get('job_description')
        job_keywords = payload.get('job_keywords', [])
        discover_new = payload.get('discover_new_projects', False)
        
        if not job_description:
            raise ValueError("job_description is required")
        
        # Import required modules
        from project_selection.relevance_engine import ProjectRelevanceEngine
        from project_selection.mimikree_project_discovery import MimikreeProjectDiscovery
        from mimikree_integration import MimikreeClient
        
        # Initialize services
        gemini_api_key = os.getenv('GOOGLE_API_KEY')
        relevance_engine = ProjectRelevanceEngine(gemini_api_key)
        
        # Get user projects from database
        with get_optimized_db_session() as session:
            from migrate_add_projects import Project
            projects = session.query(Project).filter(Project.user_id == user_id).all()
            
            projects_data = []
            current_projects = []
            alternative_projects = []
            
            for project in projects:
                proj_dict = {
                    'id': project.id,
                    'name': project.name,
                    'description': project.description,
                    'technologies': project.technologies or [],
                    'features': project.features or [],
                    'detailed_bullets': project.detailed_bullets or [],
                    'end_date': project.end_date
                }
                
                projects_data.append(proj_dict)
                
                if project.is_on_resume:
                    current_projects.append(proj_dict)
                else:
                    alternative_projects.append(proj_dict)
        
        # Score all projects
        ranked_projects = relevance_engine.rank_projects(
            projects_data,
            job_keywords,
            payload.get('required_technologies', []),
            payload.get('job_domain')
        )
        
        # Categorize results
        current_scored = []
        alternative_scored = []
        
        for project, scores in ranked_projects:
            proj_result = {
                'project': project,
                'scores': scores
            }
            
            if project['id'] in [p['id'] for p in current_projects]:
                current_scored.append(proj_result)
            else:
                alternative_scored.append(proj_result)
        
        # Get swap recommendations
        swap_recommendations = relevance_engine.recommend_project_swaps(
            current_projects,
            projects_data,
            job_keywords,
            payload.get('required_technologies', []),
            payload.get('job_domain'),
            min_improvement_threshold=15.0
        )
        
        # Discover new projects if requested
        discovered_projects = []
        if discover_new:
            try:
                # Get user's Mimikree credentials
                mimikree_credentials = mimikree_service.get_user_mimikree_credentials(user_id)
                
                if mimikree_credentials:
                    mimikree_email, mimikree_password = mimikree_credentials
                    mimikree_client = MimikreeClient()
                    mimikree_client.authenticate(mimikree_email, mimikree_password)
                    
                    discovery = MimikreeProjectDiscovery(gemini_api_key)
                    new_projects, _ = discovery.discover_projects(
                        mimikree_client,
                        job_keywords,
                        job_description,
                        current_projects,
                        max_questions=8
                    )
                    
                    discovered_projects = discovery.enrich_discovered_projects(
                        new_projects,
                        job_keywords
                    )
                    
                    logger.info(f"Discovered {len(discovered_projects)} new projects for user {user_id}")
                else:
                    logger.info(f"User {user_id} has no Mimikree credentials - skipping project discovery")
            
            except Exception as e:
                logger.error(f"Failed to discover new projects: {e}")
                # Continue without discovered projects
        
        # Log successful analysis
        security_manager.log_security_event(
            event_type=security_manager.SECURITY_EVENTS['DATA_ACCESS'],
            user_id=user_id,
            details={
                'action': 'project_analysis',
                'projects_analyzed': len(projects_data),
                'discovered_new': len(discovered_projects),
                'duration_seconds': time.time() - start_time
            }
        )
        
        execution_time = time.time() - start_time
        logger.info(f"Project analysis completed for user {user_id} in {execution_time:.2f}s")
        
        return {
            'success': True,
            'current_projects': current_scored,
            'alternative_projects': alternative_scored,
            'swap_recommendations': swap_recommendations,
            'discovered_projects': discovered_projects,
            'execution_time': execution_time
        }
    
    except Exception as e:
        error_msg = f"Project analysis failed for user {user_id}: {str(e)}"
        logger.error(error_msg)
        
        return {
            'success': False,
            'error': error_msg,
            'execution_time': time.time() - start_time
        }

# Utility function to submit jobs with proper error handling
def submit_job_with_validation(user_id: str, job_type: str, payload: Dict[str, Any], priority: JobPriority = JobPriority.NORMAL) -> Dict[str, Any]:
    """
    Submit a job with proper validation and error handling

    Args:
        user_id: User ID (UUID string)
        job_type: Type of job to submit
        payload: Job payload data
        priority: Job priority level
    """
    try:
        # Add user_id to payload
        payload['user_id'] = str(user_id)

        # Validate user exists and is active
        with get_optimized_db_session() as session:
            from database_config import User
            import uuid
            # Convert string user_id to UUID for database query
            user_uuid = uuid.UUID(str(user_id)) if not isinstance(user_id, uuid.UUID) else user_id
            user = session.query(User).filter(User.id == user_uuid, User.is_active == True).first()
            if not user:
                raise Exception("User not found or inactive")
        
        # Submit job to queue
        from job_queue import job_queue
        job_id = job_queue.submit_job(
            user_id=user_id,
            job_type=job_type,
            payload=payload,
            priority=priority
        )
        
        logger.info(f"Job {job_id} submitted successfully for user {user_id}")
        
        return {
            'success': True,
            'job_id': job_id,
            'message': f'{job_type} job submitted successfully'
        }
    
    except Exception as e:
        error_msg = f"Failed to submit {job_type} job for user {user_id}: {str(e)}"
        logger.error(error_msg)
        
        return {
            'success': False,
            'error': error_msg
        }
