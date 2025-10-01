from sqlalchemy.orm import Session
from database_config import JobListing, SessionLocal
from typing import Optional, Dict, Any, List
import logging
from datetime import datetime
import hashlib

class JobSearchService:
    
    @staticmethod
    def save_job_listings(user_id: int, jobs_data: List[Dict[str, Any]], search_source: str = "rapidapi") -> Dict[str, Any]:
        """Save job listings to PostgreSQL database"""
        db = SessionLocal()
        try:
            saved_count = 0
            updated_count = 0
            
            for job_data in jobs_data:
                # Create a unique external_id from the job data
                # Use job URL if available, otherwise create hash from title + company
                if 'job_url' in job_data and job_data['job_url']:
                    external_id = job_data['job_url']
                else:
                    # Create hash from title + company for uniqueness
                    title = job_data.get('title', '')
                    company = job_data.get('company', '')
                    unique_string = f"{title}_{company}_{search_source}"
                    external_id = hashlib.md5(unique_string.encode()).hexdigest()
                
                # Check if job listing already exists
                existing_job = db.query(JobListing).filter(
                    JobListing.external_id == external_id
                ).first()
                
                if existing_job:
                    # Update existing job listing
                    JobSearchService._update_job_listing(existing_job, job_data, search_source)
                    updated_count += 1
                else:
                    # Create new job listing
                    new_job = JobSearchService._create_job_listing(job_data, external_id, search_source)
                    db.add(new_job)
                    saved_count += 1
            
            db.commit()
            
            return {
                'success': True,
                'saved_count': saved_count,
                'updated_count': updated_count,
                'total_processed': len(jobs_data)
            }
            
        except Exception as e:
            db.rollback()
            logging.error(f"Error saving job listings: {e}")
            return {
                'success': False,
                'error': f'Failed to save job listings: {str(e)}'
            }
        finally:
            db.close()
    
    @staticmethod
    def get_job_listings(user_id: int = None, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        """Retrieve job listings from database"""
        db = SessionLocal()
        try:
            query = db.query(JobListing).filter(JobListing.is_active == True)
            
            # Get total count
            total_count = query.count()
            
            # Get paginated results
            jobs = query.order_by(JobListing.created_at.desc()).offset(offset).limit(limit).all()
            
            # Convert to dictionary format
            jobs_data = []
            for job in jobs:
                jobs_data.append(JobSearchService._job_listing_to_dict(job))
            
            return {
                'success': True,
                'jobs': jobs_data,
                'total_count': total_count,
                'limit': limit,
                'offset': offset
            }
            
        except Exception as e:
            logging.error(f"Error getting job listings: {e}")
            return {
                'success': False,
                'error': f'Failed to get job listings: {str(e)}'
            }
        finally:
            db.close()
    
    @staticmethod
    def get_recent_job_listings(hours: int = 24, limit: int = 50) -> Dict[str, Any]:
        """Get recently added job listings"""
        db = SessionLocal()
        try:
            from datetime import timedelta
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)
            
            jobs = db.query(JobListing).filter(
                JobListing.is_active == True,
                JobListing.created_at >= cutoff_time
            ).order_by(JobListing.created_at.desc()).limit(limit).all()
            
            jobs_data = [JobSearchService._job_listing_to_dict(job) for job in jobs]
            
            return {
                'success': True,
                'jobs': jobs_data,
                'count': len(jobs_data),
                'hours': hours
            }
            
        except Exception as e:
            logging.error(f"Error getting recent job listings: {e}")
            return {
                'success': False,
                'error': f'Failed to get recent job listings: {str(e)}'
            }
        finally:
            db.close()
    
    @staticmethod
    def _create_job_listing(job_data: Dict[str, Any], external_id: str, source: str) -> JobListing:
        """Create a new JobListing object from job data"""
        return JobListing(
            external_id=external_id,
            title=job_data.get('title', ''),
            company=job_data.get('company', ''),
            location=job_data.get('location', ''),
            salary=job_data.get('salary', ''),
            description=job_data.get('description', ''),
            requirements=job_data.get('requirements', ''),
            job_url=job_data.get('job_url', ''),
            source=source,
            posted_date=JobSearchService._parse_date(job_data.get('posted_date', '')),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            is_active=True
        )
    
    @staticmethod
    def _update_job_listing(job_listing: JobListing, job_data: Dict[str, Any], source: str):
        """Update existing job listing with new data"""
        job_listing.title = job_data.get('title', job_listing.title)
        job_listing.company = job_data.get('company', job_listing.company)
        job_listing.location = job_data.get('location', job_listing.location)
        job_listing.salary = job_data.get('salary', job_listing.salary)
        job_listing.description = job_data.get('description', job_listing.description)
        job_listing.requirements = job_data.get('requirements', job_listing.requirements)
        job_listing.job_url = job_data.get('job_url', job_listing.job_url)
        job_listing.source = source
        job_listing.posted_date = JobSearchService._parse_date(job_data.get('posted_date', '')) or job_listing.posted_date
        job_listing.updated_at = datetime.utcnow()
    
    @staticmethod
    def _job_listing_to_dict(job_listing: JobListing) -> Dict[str, Any]:
        """Convert JobListing object to dictionary"""
        return {
            'id': job_listing.id,
            'external_id': job_listing.external_id,
            'title': job_listing.title,
            'company': job_listing.company,
            'location': job_listing.location,
            'salary': job_listing.salary,
            'description': job_listing.description,
            'requirements': job_listing.requirements,
            'job_url': job_listing.job_url,
            'source': job_listing.source,
            'posted_date': job_listing.posted_date.isoformat() if job_listing.posted_date else None,
            'created_at': job_listing.created_at.isoformat() if job_listing.created_at else None,
            'updated_at': job_listing.updated_at.isoformat() if job_listing.updated_at else None,
            'is_active': job_listing.is_active
        }
    
    @staticmethod
    def _parse_date(date_string: str) -> Optional[datetime]:
        """Parse date string to datetime object"""
        if not date_string:
            return None
        
        try:
            # Try different date formats
            date_formats = [
                '%Y-%m-%d',
                '%Y-%m-%d %H:%M:%S',
                '%m/%d/%Y',
                '%d/%m/%Y',
                '%Y-%m-%dT%H:%M:%S',
                '%Y-%m-%dT%H:%M:%SZ'
            ]
            
            for fmt in date_formats:
                try:
                    return datetime.strptime(date_string, fmt)
                except ValueError:
                    continue
            
            # If none of the formats work, return None
            logging.warning(f"Could not parse date: {date_string}")
            return None
            
        except Exception as e:
            logging.warning(f"Error parsing date {date_string}: {e}")
            return None
