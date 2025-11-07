"""
Database Performance Optimization for Job Application Agent
Implements connection pooling, indexing, and performance monitoring
"""

import os
import logging
import time
from contextlib import contextmanager
from typing import Dict, Any, List, Optional
from sqlalchemy import create_engine, text, Index, event
from sqlalchemy.pool import QueuePool, StaticPool
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.engine import Engine
import psycopg2
from psycopg2 import pool
from datetime import datetime, timedelta
import threading

class DatabaseOptimizer:
    """
    Database performance optimizer with connection pooling and monitoring
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.connection_pool = None
        self.engine = None
        self.SessionLocal = None
        self._setup_optimized_engine()
        self._setup_monitoring()
    
    def _setup_optimized_engine(self):
        """Set up optimized database engine with connection pooling"""
        
        # Database configuration
        DB_HOST = os.getenv('DB_HOST', 'localhost')
        DB_PORT = os.getenv('DB_PORT', '5432')
        DB_NAME = os.getenv('DB_NAME', 'job_agent_db')
        DB_USER = os.getenv('DB_USER', 'postgres')
        DB_PASSWORD = os.getenv('DB_PASSWORD')
        
        if not DB_PASSWORD:
            raise ValueError("DB_PASSWORD environment variable is required")
        
        # Create optimized database URL
        from urllib.parse import quote_plus
        encoded_password = quote_plus(DB_PASSWORD)
        DATABASE_URL = f"postgresql://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        
        # Connection pool settings
        pool_settings = {
            'poolclass': QueuePool,
            'pool_size': int(os.getenv('DB_POOL_SIZE', 10)),  # Base connections
            'max_overflow': int(os.getenv('DB_MAX_OVERFLOW', 20)),  # Additional connections
            'pool_timeout': int(os.getenv('DB_POOL_TIMEOUT', 30)),  # Timeout for getting connection
            'pool_recycle': int(os.getenv('DB_POOL_RECYCLE', 3600)),  # Recycle connections every hour
            'pool_pre_ping': True,  # Validate connections before use
        }
        
        # Create engine with optimizations
        self.engine = create_engine(
            DATABASE_URL,
            echo=os.getenv('DB_ECHO', 'false').lower() == 'true',
            **pool_settings
        )
        
        # Create session factory
        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine
        )
        
        self.logger.info(f"Database engine created with pool_size={pool_settings['pool_size']}, max_overflow={pool_settings['max_overflow']}")
    
    def _setup_monitoring(self):
        """Set up database performance monitoring"""
        
        @event.listens_for(Engine, "before_cursor_execute")
        def receive_before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            context._query_start_time = time.time()
        
        @event.listens_for(Engine, "after_cursor_execute")
        def receive_after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            total = time.time() - context._query_start_time
            
            # Log slow queries (>1 second)
            if total > 1.0:
                self.logger.warning(f"Slow query detected: {total:.2f}s - {statement[:100]}...")
            
            # Log very slow queries (>5 seconds)
            if total > 5.0:
                self.logger.error(f"Very slow query: {total:.2f}s - {statement}")
    
    @contextmanager
    def get_db_session(self):
        """Get database session with proper cleanup"""
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            self.logger.error(f"Database session error: {e}")
            raise
        finally:
            session.close()
    
    def create_performance_indexes(self):
        """Create performance indexes for common queries"""
        
        indexes_to_create = [
            # Users table indexes
            {
                'table': 'users',
                'name': 'idx_users_email_active',
                'columns': ['email', 'is_active'],
                'description': 'Optimize user login queries'
            },
            {
                'table': 'users',
                'name': 'idx_users_created_at',
                'columns': ['created_at'],
                'description': 'Optimize user registration analytics'
            },
            
            # User profiles indexes
            {
                'table': 'user_profiles',
                'name': 'idx_user_profiles_user_id',
                'columns': ['user_id'],
                'description': 'Optimize profile lookups'
            },
            
            # Job applications indexes
            {
                'table': 'job_applications',
                'name': 'idx_job_applications_user_status',
                'columns': ['user_id', 'status'],
                'description': 'Optimize user job application queries'
            },
            {
                'table': 'job_applications',
                'name': 'idx_job_applications_created_at',
                'columns': ['created_at'],
                'description': 'Optimize recent applications queries'
            },
            
            # Job listings indexes
            {
                'table': 'job_listings',
                'name': 'idx_job_listings_active_created',
                'columns': ['is_active', 'created_at'],
                'description': 'Optimize active job listings queries'
            },
            {
                'table': 'job_listings',
                'name': 'idx_job_listings_relevance_score',
                'columns': ['relevance_score'],
                'description': 'Optimize job relevance sorting'
            },
            
            # Projects indexes
            {
                'table': 'projects',
                'name': 'idx_projects_user_resume',
                'columns': ['user_id', 'is_on_resume'],
                'description': 'Optimize user project queries'
            },
            {
                'table': 'projects',
                'name': 'idx_projects_relevance_used',
                'columns': ['avg_relevance_score', 'times_used'],
                'description': 'Optimize project selection queries'
            },
            
            # Action history indexes
            {
                'table': 'action_history',
                'name': 'idx_action_history_user_job',
                'columns': ['user_id', 'job_id'],
                'description': 'Optimize action history lookups'
            },
            {
                'table': 'action_history',
                'name': 'idx_action_history_expires_at',
                'columns': ['expires_at'],
                'description': 'Optimize expired record cleanup'
            },
            
            # Project usage history indexes
            {
                'table': 'project_usage_history',
                'name': 'idx_project_usage_user_project',
                'columns': ['user_id', 'project_id'],
                'description': 'Optimize project usage tracking'
            },
            {
                'table': 'project_usage_history',
                'name': 'idx_project_usage_created_at',
                'columns': ['created_at'],
                'description': 'Optimize usage analytics'
            }
        ]
        
        created_count = 0
        
        # Create indexes individually with autocommit to avoid transaction issues
        for index_config in indexes_to_create:
            try:
                # Use raw connection with autocommit for index creation
                with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    # Check if index already exists
                    check_query = text("""
                        SELECT 1 FROM pg_indexes 
                        WHERE indexname = :index_name
                    """)
                    
                    result = conn.execute(check_query, {'index_name': index_config['name']})
                    if result.fetchone():
                        self.logger.debug(f"Index {index_config['name']} already exists")
                        continue
                    
                    # Check if table exists
                    table_check = text("""
                        SELECT 1 FROM pg_tables 
                        WHERE tablename = :table_name
                    """)
                    result = conn.execute(table_check, {'table_name': index_config['table']})
                    if not result.fetchone():
                        self.logger.debug(f"Table {index_config['table']} doesn't exist yet, skipping index")
                        continue
                    
                    # Create index (without CONCURRENTLY to work in all scenarios)
                    columns_str = ', '.join(index_config['columns'])
                    create_query = text(f"""
                        CREATE INDEX IF NOT EXISTS {index_config['name']} 
                        ON {index_config['table']} ({columns_str})
                    """)
                    
                    conn.execute(create_query)
                    
                    created_count += 1
                    self.logger.info(f"Created index {index_config['name']}: {index_config['description']}")
                    
            except Exception as e:
                self.logger.warning(f"Failed to create index {index_config['name']}: {e}")
        
        self.logger.info(f"Database indexing complete. Created {created_count} new indexes.")
    
    def analyze_table_statistics(self):
        """Update table statistics for query optimizer"""
        
        tables_to_analyze = [
            'users', 'user_profiles', 'job_applications', 
            'job_listings', 'projects', 'project_usage_history', 
            'action_history'
        ]
        
        with self.get_db_session() as session:
            for table in tables_to_analyze:
                try:
                    session.execute(text(f"ANALYZE {table}"))
                    self.logger.debug(f"Analyzed table statistics for {table}")
                except Exception as e:
                    self.logger.error(f"Failed to analyze table {table}: {e}")
            
            session.commit()
        
        self.logger.info("Table statistics analysis complete")
    
    def get_connection_pool_stats(self) -> Dict[str, Any]:
        """Get connection pool statistics"""
        if not self.engine or not hasattr(self.engine.pool, 'size'):
            return {"error": "Connection pool not available"}
        
        pool = self.engine.pool
        
        return {
            "pool_size": pool.size(),
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
            "invalid": pool.invalid(),
            "total_connections": pool.size() + pool.overflow(),
            "utilization_percent": round((pool.checkedout() / (pool.size() + pool.overflow())) * 100, 2) if (pool.size() + pool.overflow()) > 0 else 0
        }
    
    def get_slow_queries(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get slow queries from PostgreSQL logs"""
        
        with self.get_db_session() as session:
            try:
                # Enable pg_stat_statements if available
                session.execute(text("CREATE EXTENSION IF NOT EXISTS pg_stat_statements"))
                
                # Get slow queries
                query = text("""
                    SELECT 
                        query,
                        calls,
                        total_time,
                        mean_time,
                        rows,
                        100.0 * shared_blks_hit / nullif(shared_blks_hit + shared_blks_read, 0) AS hit_percent
                    FROM pg_stat_statements
                    WHERE query NOT LIKE '%pg_stat_statements%'
                    ORDER BY mean_time DESC
                    LIMIT :limit
                """)
                
                result = session.execute(query, {'limit': limit})
                
                slow_queries = []
                for row in result:
                    slow_queries.append({
                        'query': row.query[:200] + '...' if len(row.query) > 200 else row.query,
                        'calls': row.calls,
                        'total_time': round(row.total_time, 2),
                        'mean_time': round(row.mean_time, 2),
                        'rows': row.rows,
                        'hit_percent': round(row.hit_percent or 0, 2)
                    })
                
                return slow_queries
                
            except Exception as e:
                self.logger.error(f"Failed to get slow queries: {e}")
                return []
    
    def cleanup_expired_records(self):
        """Clean up expired records to maintain performance"""
        
        cleanup_tasks = [
            {
                'table': 'action_history',
                'condition': 'expires_at < NOW()',
                'description': 'expired action history records'
            },
            {
                'table': 'job_applications',
                'condition': "status = 'completed' AND created_at < NOW() - INTERVAL '30 days'",
                'description': 'old completed job applications'
            }
        ]
        
        total_deleted = 0
        
        with self.get_db_session() as session:
            for task in cleanup_tasks:
                try:
                    delete_query = text(f"DELETE FROM {task['table']} WHERE {task['condition']}")
                    result = session.execute(delete_query)
                    deleted_count = result.rowcount
                    
                    total_deleted += deleted_count
                    self.logger.info(f"Cleaned up {deleted_count} {task['description']}")
                    
                except Exception as e:
                    self.logger.error(f"Failed to cleanup {task['table']}: {e}")
            
            session.commit()
        
        self.logger.info(f"Database cleanup complete. Deleted {total_deleted} records total.")
        return total_deleted
    
    def vacuum_analyze_tables(self):
        """Run VACUUM ANALYZE on all tables for optimal performance"""
        
        with self.get_db_session() as session:
            try:
                # Get all user tables
                tables_query = text("""
                    SELECT tablename FROM pg_tables 
                    WHERE schemaname = 'public'
                    AND tablename NOT LIKE 'pg_%'
                """)
                
                result = session.execute(tables_query)
                tables = [row.tablename for row in result]
                
                for table in tables:
                    try:
                        session.execute(text(f"VACUUM ANALYZE {table}"))
                        self.logger.debug(f"VACUUM ANALYZE completed for {table}")
                    except Exception as e:
                        self.logger.error(f"VACUUM ANALYZE failed for {table}: {e}")
                
                session.commit()
                self.logger.info(f"VACUUM ANALYZE completed for {len(tables)} tables")
                
            except Exception as e:
                self.logger.error(f"Failed to run VACUUM ANALYZE: {e}")

# Global database optimizer instance
db_optimizer = DatabaseOptimizer()

# Utility functions
def get_optimized_db_session():
    """Get optimized database session"""
    return db_optimizer.get_db_session()

def setup_database_optimizations():
    """Set up all database optimizations"""
    db_optimizer.create_performance_indexes()
    db_optimizer.analyze_table_statistics()
    db_optimizer.logger.info("Database optimizations setup complete")

def get_database_health() -> Dict[str, Any]:
    """Get comprehensive database health metrics"""
    return {
        "connection_pool": db_optimizer.get_connection_pool_stats(),
        "slow_queries": db_optimizer.get_slow_queries(5),
        "timestamp": datetime.utcnow().isoformat()
    }

# Scheduled maintenance functions
def run_daily_maintenance():
    """Run daily database maintenance tasks"""
    db_optimizer.cleanup_expired_records()
    db_optimizer.analyze_table_statistics()

def run_weekly_maintenance():
    """Run weekly database maintenance tasks"""
    db_optimizer.vacuum_analyze_tables()
    db_optimizer.create_performance_indexes()  # Create any new indexes
