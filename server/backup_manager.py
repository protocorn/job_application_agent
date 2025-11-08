"""
Automated Backup and Recovery Manager for Job Application Agent
Implements database backups, file backups, and disaster recovery procedures
"""

import os
import logging
import subprocess
import shutil
import gzip
import json
import boto3
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
import schedule
import threading
import time
from pathlib import Path
import hashlib
import psycopg2
from sqlalchemy import create_engine, text
import redis

class BackupManager:
    """
    Comprehensive backup and recovery manager
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Backup configuration
        self.backup_config = {
            'database': {
                'enabled': True,
                'retention_days': 30,
                'schedule': '0 2 * * *',  # Daily at 2 AM
                'compress': True
            },
            'files': {
                'enabled': True,
                'retention_days': 7,
                'schedule': '0 3 * * *',  # Daily at 3 AM
                'compress': True
            },
            'logs': {
                'enabled': True,
                'retention_days': 14,
                'schedule': '0 4 * * 0',  # Weekly on Sunday at 4 AM
                'compress': True
            }
        }
        
        # Storage configuration
        self.storage_config = {
            'local_backup_dir': os.getenv('BACKUP_DIR', './backups'),
            'aws_s3_bucket': os.getenv('AWS_S3_BACKUP_BUCKET'),
            'aws_region': os.getenv('AWS_REGION', 'us-east-1'),
            'encryption_enabled': os.getenv('BACKUP_ENCRYPTION', 'true').lower() == 'true'
        }
        
        # Database configuration
        self.db_config = {
            'host': os.getenv('DB_HOST', 'localhost'),
            'port': os.getenv('DB_PORT', '5432'),
            'name': os.getenv('DB_NAME', 'job_agent_db'),
            'user': os.getenv('DB_USER', 'postgres'),
            'password': os.getenv('DB_PASSWORD')
        }
        
        # Initialize storage
        self._setup_local_storage()
        self._setup_cloud_storage()
        
        # Redis for backup status tracking
        # Support both local Redis and Upstash (with TLS)
        REDIS_URL = os.getenv('REDIS_URL')
        
        if REDIS_URL:
            # Use Redis URL with different DB for backups
            import urllib.parse
            
            # Convert to rediss:// for TLS if using Upstash
            redis_url = REDIS_URL
            if redis_url.startswith('redis://') and 'upstash.io' in redis_url:
                redis_url = redis_url.replace('redis://', 'rediss://', 1)
            
            parsed = urllib.parse.urlparse(redis_url)
            redis_url_with_db = f"{parsed.scheme}://{parsed.netloc}/3"  # DB 3 for backups
            
            self.redis_client = redis.from_url(
                redis_url_with_db,
                decode_responses=True
            )
        else:
            # Use individual connection parameters (for local Redis)
            self.redis_client = redis.Redis(
                host=os.getenv('REDIS_HOST', 'localhost'),
                port=int(os.getenv('REDIS_PORT', 6379)),
                db=0,  # Only database 0 is supported in some Redis configurations
                decode_responses=True
            )
    
    def _setup_local_storage(self):
        """Set up local backup storage"""
        backup_dir = Path(self.storage_config['local_backup_dir'])
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        # Create subdirectories
        for backup_type in ['database', 'files', 'logs']:
            (backup_dir / backup_type).mkdir(exist_ok=True)
        
        self.logger.info(f"Local backup storage initialized at {backup_dir}")
    
    def _setup_cloud_storage(self):
        """Set up cloud storage (AWS S3)"""
        if self.storage_config['aws_s3_bucket']:
            try:
                self.s3_client = boto3.client(
                    's3',
                    region_name=self.storage_config['aws_region']
                )
                
                # Test connection
                self.s3_client.head_bucket(Bucket=self.storage_config['aws_s3_bucket'])
                self.logger.info(f"Cloud backup storage connected to S3 bucket: {self.storage_config['aws_s3_bucket']}")
                
            except Exception as e:
                self.logger.warning(f"Cloud storage setup failed: {e}")
                self.s3_client = None
        else:
            self.s3_client = None
            self.logger.info("Cloud storage not configured")
    
    def backup_database(self) -> Dict[str, Any]:
        """
        Create database backup using pg_dump
        
        Returns:
            Backup result with status and details
        """
        backup_id = f"db_backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        
        try:
            self.logger.info(f"Starting database backup: {backup_id}")
            
            # Create backup filename
            backup_filename = f"{backup_id}.sql"
            if self.backup_config['database']['compress']:
                backup_filename += ".gz"
            
            backup_path = Path(self.storage_config['local_backup_dir']) / 'database' / backup_filename
            
            # Build pg_dump command
            # Note: Using pg_dumpall or finding correct pg_dump version for PostgreSQL 17.6
            # Using --no-sync to avoid version-specific issues
            pg_dump_cmd = [
                'pg_dump',
                f"--host={self.db_config['host']}",
                f"--port={self.db_config['port']}",
                f"--username={self.db_config['user']}",
                '--verbose',
                '--clean',
                '--no-owner',
                '--no-privileges',
                '--no-sync',  # Avoid fsync to reduce version compatibility issues
                '--format=custom' if not self.backup_config['database']['compress'] else '--format=plain',
                self.db_config['name']
            ]
            
            # Set password via environment
            env = os.environ.copy()
            env['PGPASSWORD'] = self.db_config['password']
            
            # Execute backup
            start_time = time.time()
            
            if self.backup_config['database']['compress']:
                # Pipe through gzip
                with open(backup_path, 'wb') as f:
                    pg_dump = subprocess.Popen(
                        pg_dump_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=env
                    )
                    
                    gzip_proc = subprocess.Popen(
                        ['gzip'],
                        stdin=pg_dump.stdout,
                        stdout=f,
                        stderr=subprocess.PIPE
                    )
                    
                    pg_dump.stdout.close()
                    gzip_proc.communicate()

                    if pg_dump.wait() != 0:
                        error_msg = pg_dump.stderr.read().decode()
                        if "server version mismatch" in error_msg:
                            raise Exception(
                                f"pg_dump version mismatch: {error_msg}\n"
                                "Please upgrade pg_dump to match PostgreSQL server version 17.6. "
                                "You can install it with: apt-get install postgresql-client-17 or brew install postgresql@17"
                            )
                        raise Exception(f"pg_dump failed: {error_msg}")
            else:
                # Direct backup
                with open(backup_path, 'wb') as f:
                    try:
                        result = subprocess.run(
                            pg_dump_cmd,
                            stdout=f,
                            stderr=subprocess.PIPE,
                            env=env,
                            check=True
                        )
                    except subprocess.CalledProcessError as e:
                        error_msg = e.stderr.decode() if e.stderr else str(e)
                        if "server version mismatch" in error_msg:
                            raise Exception(
                                f"pg_dump version mismatch: {error_msg}\n"
                                "Please upgrade pg_dump to match PostgreSQL server version 17.6. "
                                "You can install it with: apt-get install postgresql-client-17 or brew install postgresql@17"
                            )
                        raise Exception(f"pg_dump failed: {error_msg}")
            
            backup_time = time.time() - start_time
            backup_size = backup_path.stat().st_size
            
            # Calculate checksum
            checksum = self._calculate_file_checksum(backup_path)
            
            # Create backup metadata
            metadata = {
                'backup_id': backup_id,
                'type': 'database',
                'timestamp': datetime.utcnow().isoformat(),
                'filename': backup_filename,
                'size_bytes': backup_size,
                'size_mb': round(backup_size / (1024 * 1024), 2),
                'duration_seconds': round(backup_time, 2),
                'checksum': checksum,
                'compressed': self.backup_config['database']['compress'],
                'status': 'completed'
            }
            
            # Save metadata
            metadata_path = backup_path.with_suffix('.json')
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            # Upload to cloud storage if configured
            if self.s3_client:
                self._upload_to_s3(backup_path, f"database/{backup_filename}")
                self._upload_to_s3(metadata_path, f"database/{backup_filename}.json")
                metadata['cloud_uploaded'] = True
            
            # Store backup info in Redis
            self.redis_client.hset(f"backup:{backup_id}", mapping=metadata)
            self.redis_client.expire(f"backup:{backup_id}", 86400 * self.backup_config['database']['retention_days'])
            
            self.logger.info(f"Database backup completed: {backup_id} ({metadata['size_mb']}MB in {metadata['duration_seconds']}s)")
            
            return {
                'success': True,
                'backup_id': backup_id,
                'metadata': metadata
            }
            
        except Exception as e:
            error_msg = f"Database backup failed: {e}"
            self.logger.error(error_msg)
            
            # Store failure info
            failure_metadata = {
                'backup_id': backup_id,
                'type': 'database',
                'timestamp': datetime.utcnow().isoformat(),
                'status': 'failed',
                'error': str(e)
            }
            
            self.redis_client.hset(f"backup:{backup_id}", mapping=failure_metadata)
            self.redis_client.expire(f"backup:{backup_id}", 86400)
            
            return {
                'success': False,
                'backup_id': backup_id,
                'error': error_msg
            }
    
    def backup_files(self, directories: List[str] = None) -> Dict[str, Any]:
        """
        Create backup of important files and directories
        
        Args:
            directories: List of directories to backup (defaults to important app directories)
        """
        backup_id = f"files_backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        
        if directories is None:
            directories = [
                './Resumes',
                './Cache',
                './server/sessions',
                './ProfileBuilder'
            ]
        
        try:
            self.logger.info(f"Starting files backup: {backup_id}")
            
            backup_filename = f"{backup_id}.tar"
            if self.backup_config['files']['compress']:
                backup_filename += ".gz"
            
            backup_path = Path(self.storage_config['local_backup_dir']) / 'files' / backup_filename
            
            start_time = time.time()
            
            # Create tar archive
            import tarfile
            
            mode = 'w:gz' if self.backup_config['files']['compress'] else 'w'
            
            with tarfile.open(backup_path, mode) as tar:
                for directory in directories:
                    if os.path.exists(directory):
                        tar.add(directory, arcname=os.path.basename(directory))
                        self.logger.debug(f"Added {directory} to backup")
                    else:
                        self.logger.warning(f"Directory not found: {directory}")
            
            backup_time = time.time() - start_time
            backup_size = backup_path.stat().st_size
            checksum = self._calculate_file_checksum(backup_path)
            
            # Create metadata
            metadata = {
                'backup_id': backup_id,
                'type': 'files',
                'timestamp': datetime.utcnow().isoformat(),
                'filename': backup_filename,
                'directories': directories,
                'size_bytes': backup_size,
                'size_mb': round(backup_size / (1024 * 1024), 2),
                'duration_seconds': round(backup_time, 2),
                'checksum': checksum,
                'compressed': self.backup_config['files']['compress'],
                'status': 'completed'
            }
            
            # Save metadata
            metadata_path = backup_path.with_suffix('.json')
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            # Upload to cloud if configured
            if self.s3_client:
                self._upload_to_s3(backup_path, f"files/{backup_filename}")
                self._upload_to_s3(metadata_path, f"files/{backup_filename}.json")
                metadata['cloud_uploaded'] = True
            
            # Store in Redis
            self.redis_client.hset(f"backup:{backup_id}", mapping=metadata)
            self.redis_client.expire(f"backup:{backup_id}", 86400 * self.backup_config['files']['retention_days'])
            
            self.logger.info(f"Files backup completed: {backup_id} ({metadata['size_mb']}MB in {metadata['duration_seconds']}s)")
            
            return {
                'success': True,
                'backup_id': backup_id,
                'metadata': metadata
            }
            
        except Exception as e:
            error_msg = f"Files backup failed: {e}"
            self.logger.error(error_msg)
            
            return {
                'success': False,
                'backup_id': backup_id,
                'error': error_msg
            }
    
    def backup_logs(self) -> Dict[str, Any]:
        """Create backup of application logs"""
        backup_id = f"logs_backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        
        try:
            self.logger.info(f"Starting logs backup: {backup_id}")
            
            log_directories = [
                './logs',
                './server/logs',
                './Agents/logs'
            ]
            
            backup_filename = f"{backup_id}.tar.gz"
            backup_path = Path(self.storage_config['local_backup_dir']) / 'logs' / backup_filename
            
            start_time = time.time()
            
            # Create compressed tar archive
            import tarfile
            
            with tarfile.open(backup_path, 'w:gz') as tar:
                for log_dir in log_directories:
                    if os.path.exists(log_dir):
                        tar.add(log_dir, arcname=os.path.basename(log_dir))
            
            backup_time = time.time() - start_time
            backup_size = backup_path.stat().st_size
            checksum = self._calculate_file_checksum(backup_path)
            
            metadata = {
                'backup_id': backup_id,
                'type': 'logs',
                'timestamp': datetime.utcnow().isoformat(),
                'filename': backup_filename,
                'directories': log_directories,
                'size_bytes': backup_size,
                'size_mb': round(backup_size / (1024 * 1024), 2),
                'duration_seconds': round(backup_time, 2),
                'checksum': checksum,
                'status': 'completed'
            }
            
            # Save metadata
            metadata_path = backup_path.with_suffix('.json')
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            # Upload to cloud
            if self.s3_client:
                self._upload_to_s3(backup_path, f"logs/{backup_filename}")
                self._upload_to_s3(metadata_path, f"logs/{backup_filename}.json")
                metadata['cloud_uploaded'] = True
            
            # Store in Redis
            self.redis_client.hset(f"backup:{backup_id}", mapping=metadata)
            self.redis_client.expire(f"backup:{backup_id}", 86400 * self.backup_config['logs']['retention_days'])
            
            self.logger.info(f"Logs backup completed: {backup_id}")
            
            return {
                'success': True,
                'backup_id': backup_id,
                'metadata': metadata
            }
            
        except Exception as e:
            error_msg = f"Logs backup failed: {e}"
            self.logger.error(error_msg)
            
            return {
                'success': False,
                'backup_id': backup_id,
                'error': error_msg
            }
    
    def restore_database(self, backup_id: str) -> Dict[str, Any]:
        """
        Restore database from backup
        
        Args:
            backup_id: ID of backup to restore
        """
        try:
            self.logger.info(f"Starting database restore from backup: {backup_id}")
            
            # Get backup metadata
            backup_info = self.redis_client.hgetall(f"backup:{backup_id}")
            if not backup_info:
                raise Exception(f"Backup {backup_id} not found")
            
            if backup_info.get('type') != 'database':
                raise Exception(f"Backup {backup_id} is not a database backup")
            
            backup_filename = backup_info['filename']
            backup_path = Path(self.storage_config['local_backup_dir']) / 'database' / backup_filename
            
            # Download from cloud if not available locally
            if not backup_path.exists() and self.s3_client:
                self.logger.info("Downloading backup from cloud storage")
                self.s3_client.download_file(
                    self.storage_config['aws_s3_bucket'],
                    f"database/{backup_filename}",
                    str(backup_path)
                )
            
            if not backup_path.exists():
                raise Exception(f"Backup file not found: {backup_path}")
            
            # Verify checksum
            if 'checksum' in backup_info:
                current_checksum = self._calculate_file_checksum(backup_path)
                if current_checksum != backup_info['checksum']:
                    raise Exception("Backup file checksum mismatch - file may be corrupted")
            
            # Create restore command
            if backup_info.get('compressed') == 'True':
                # Decompress and restore
                restore_cmd = [
                    'gunzip', '-c', str(backup_path), '|',
                    'psql',
                    f"--host={self.db_config['host']}",
                    f"--port={self.db_config['port']}",
                    f"--username={self.db_config['user']}",
                    self.db_config['name']
                ]
                
                # Use shell to handle pipe
                restore_cmd_str = ' '.join(restore_cmd)
                
                env = os.environ.copy()
                env['PGPASSWORD'] = self.db_config['password']
                
                result = subprocess.run(
                    restore_cmd_str,
                    shell=True,
                    env=env,
                    capture_output=True,
                    text=True
                )
            else:
                # Direct restore using pg_restore
                restore_cmd = [
                    'pg_restore',
                    f"--host={self.db_config['host']}",
                    f"--port={self.db_config['port']}",
                    f"--username={self.db_config['user']}",
                    '--clean',
                    '--if-exists',
                    '--dbname', self.db_config['name'],
                    str(backup_path)
                ]
                
                env = os.environ.copy()
                env['PGPASSWORD'] = self.db_config['password']
                
                result = subprocess.run(
                    restore_cmd,
                    env=env,
                    capture_output=True,
                    text=True
                )
            
            if result.returncode != 0:
                raise Exception(f"Database restore failed: {result.stderr}")
            
            self.logger.info(f"Database restore completed successfully from backup: {backup_id}")
            
            return {
                'success': True,
                'backup_id': backup_id,
                'message': 'Database restored successfully'
            }
            
        except Exception as e:
            error_msg = f"Database restore failed: {e}"
            self.logger.error(error_msg)
            
            return {
                'success': False,
                'backup_id': backup_id,
                'error': error_msg
            }
    
    def list_backups(self, backup_type: str = None) -> List[Dict[str, Any]]:
        """List available backups"""
        try:
            # Get all backup keys from Redis
            backup_keys = self.redis_client.keys("backup:*")
            backups = []
            
            for key in backup_keys:
                backup_info = self.redis_client.hgetall(key)
                if backup_info:
                    if backup_type is None or backup_info.get('type') == backup_type:
                        backups.append(backup_info)
            
            # Sort by timestamp (newest first)
            backups.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            
            return backups
            
        except Exception as e:
            self.logger.error(f"Error listing backups: {e}")
            return []
    
    def cleanup_old_backups(self):
        """Clean up old backups based on retention policy"""
        try:
            self.logger.info("Starting backup cleanup")
            
            for backup_type in ['database', 'files', 'logs']:
                retention_days = self.backup_config[backup_type]['retention_days']
                cutoff_date = datetime.utcnow() - timedelta(days=retention_days)
                
                backups = self.list_backups(backup_type)
                deleted_count = 0
                
                for backup in backups:
                    backup_date = datetime.fromisoformat(backup['timestamp'])
                    
                    if backup_date < cutoff_date:
                        # Delete local file
                        backup_path = Path(self.storage_config['local_backup_dir']) / backup_type / backup['filename']
                        if backup_path.exists():
                            backup_path.unlink()
                            
                            # Delete metadata file
                            metadata_path = backup_path.with_suffix('.json')
                            if metadata_path.exists():
                                metadata_path.unlink()
                        
                        # Delete from cloud storage
                        if self.s3_client and backup.get('cloud_uploaded'):
                            try:
                                self.s3_client.delete_object(
                                    Bucket=self.storage_config['aws_s3_bucket'],
                                    Key=f"{backup_type}/{backup['filename']}"
                                )
                                self.s3_client.delete_object(
                                    Bucket=self.storage_config['aws_s3_bucket'],
                                    Key=f"{backup_type}/{backup['filename']}.json"
                                )
                            except Exception as e:
                                self.logger.warning(f"Failed to delete cloud backup: {e}")
                        
                        # Delete from Redis
                        self.redis_client.delete(f"backup:{backup['backup_id']}")
                        
                        deleted_count += 1
                        self.logger.debug(f"Deleted old backup: {backup['backup_id']}")
                
                self.logger.info(f"Cleaned up {deleted_count} old {backup_type} backups")
            
        except Exception as e:
            self.logger.error(f"Backup cleanup failed: {e}")
    
    def _calculate_file_checksum(self, file_path: Path) -> str:
        """Calculate SHA256 checksum of file"""
        sha256_hash = hashlib.sha256()
        
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        
        return sha256_hash.hexdigest()
    
    def _upload_to_s3(self, local_path: Path, s3_key: str):
        """Upload file to S3"""
        if not self.s3_client:
            return
        
        try:
            self.s3_client.upload_file(
                str(local_path),
                self.storage_config['aws_s3_bucket'],
                s3_key
            )
            self.logger.debug(f"Uploaded {local_path} to S3: {s3_key}")
            
        except Exception as e:
            self.logger.error(f"Failed to upload to S3: {e}")
            raise
    
    def get_backup_status(self) -> Dict[str, Any]:
        """Get backup system status"""
        try:
            backups = self.list_backups()
            
            # Group by type
            backup_counts = {'database': 0, 'files': 0, 'logs': 0}
            total_size = 0
            latest_backups = {}
            
            for backup in backups:
                backup_type = backup.get('type', 'unknown')
                if backup_type in backup_counts:
                    backup_counts[backup_type] += 1
                    
                    if backup.get('size_bytes'):
                        total_size += int(backup['size_bytes'])
                    
                    if backup_type not in latest_backups:
                        latest_backups[backup_type] = backup['timestamp']
            
            return {
                'backup_counts': backup_counts,
                'total_backups': sum(backup_counts.values()),
                'total_size_mb': round(total_size / (1024 * 1024), 2),
                'latest_backups': latest_backups,
                'cloud_storage_enabled': self.s3_client is not None,
                'local_storage_path': self.storage_config['local_backup_dir']
            }
            
        except Exception as e:
            self.logger.error(f"Error getting backup status: {e}")
            return {'error': str(e)}

# Global backup manager instance
backup_manager = BackupManager()

# Utility functions
def run_full_backup() -> Dict[str, Any]:
    """Run full system backup (database + files + logs)"""
    results = {
        'database': backup_manager.backup_database(),
        'files': backup_manager.backup_files(),
        'logs': backup_manager.backup_logs()
    }
    
    success_count = sum(1 for result in results.values() if result['success'])
    
    return {
        'success': success_count == len(results),
        'results': results,
        'summary': f"{success_count}/{len(results)} backups completed successfully"
    }

def schedule_backups():
    """Set up automated backup scheduling"""
    # Schedule database backups
    schedule.every().day.at("02:00").do(backup_manager.backup_database)
    
    # Schedule file backups
    schedule.every().day.at("03:00").do(backup_manager.backup_files)
    
    # Schedule log backups
    schedule.every().sunday.at("04:00").do(backup_manager.backup_logs)
    
    # Schedule cleanup
    schedule.every().day.at("05:00").do(backup_manager.cleanup_old_backups)
    
    # Start scheduler in background thread
    def run_scheduler():
        while True:
            schedule.run_pending()
            time.sleep(60)  # Check every minute
    
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    
    backup_manager.logger.info("Backup scheduler started")
