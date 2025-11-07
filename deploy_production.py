"""
Production Deployment Script for Job Application Agent
Handles database setup, security configuration, and infrastructure initialization
"""

import os
import sys
import subprocess
import logging
import json
from pathlib import Path
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('deployment.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

class ProductionDeployer:
    """
    Handles production deployment of Job Application Agent
    """
    
    def __init__(self):
        self.project_root = Path(__file__).parent
        self.required_env_vars = [
            'DB_PASSWORD',
            'JWT_SECRET_KEY', 
            'ENCRYPTION_KEY',
            'GOOGLE_API_KEY',
            'GOOGLE_CLIENT_ID',
            'GOOGLE_CLIENT_SECRET',
            'MIMIKREE_EMAIL',
            'MIMIKREE_PASSWORD'
        ]
    
    def check_prerequisites(self):
        """Check if all prerequisites are met"""
        logger.info("🔍 Checking prerequisites...")
        
        # Check Python version
        if sys.version_info < (3, 8):
            raise Exception("Python 3.8 or higher is required")
        
        # Check required environment variables
        missing_vars = []
        for var in self.required_env_vars:
            if not os.getenv(var):
                missing_vars.append(var)
        
        if missing_vars:
            logger.error(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
            logger.info("Please set these in your .env file or environment")
            return False
        
        # Check if Redis is available
        try:
            import redis
            r = redis.Redis(host='localhost', port=6379, db=0)
            r.ping()
            logger.info("✅ Redis connection successful")
        except Exception as e:
            logger.error(f"❌ Redis connection failed: {e}")
            logger.info("Please install and start Redis server")
            return False
        
        # Check if PostgreSQL is available
        try:
            import psycopg2
            conn = psycopg2.connect(
                host=os.getenv('DB_HOST', 'localhost'),
                port=os.getenv('DB_PORT', '5432'),
                database=os.getenv('DB_NAME', 'job_agent_db'),
                user=os.getenv('DB_USER', 'postgres'),
                password=os.getenv('DB_PASSWORD')
            )
            conn.close()
            logger.info("✅ PostgreSQL connection successful")
        except Exception as e:
            logger.error(f"❌ PostgreSQL connection failed: {e}")
            logger.info("Please ensure PostgreSQL is running and credentials are correct")
            return False
        
        logger.info("✅ All prerequisites met")
        return True
    
    def install_dependencies(self):
        """Install production dependencies"""
        logger.info("📦 Installing production dependencies...")
        
        try:
            # Install production requirements
            subprocess.run([
                sys.executable, '-m', 'pip', 'install', '-r', 'requirements_production.txt'
            ], check=True, cwd=self.project_root)
            
            logger.info("✅ Dependencies installed successfully")
            return True
            
        except subprocess.CalledProcessError as e:
            logger.error(f"❌ Failed to install dependencies: {e}")
            return False
    
    def setup_database(self):
        """Set up database with optimizations"""
        logger.info("🗄️ Setting up database...")
        
        try:
            # Run existing migrations
            migrations = [
                'migrate_database.py',
                'migrate_add_projects.py'
            ]
            
            for migration in migrations:
                migration_path = self.project_root / migration
                if migration_path.exists():
                    logger.info(f"Running migration: {migration}")
                    subprocess.run([sys.executable, str(migration_path)], check=True)
            
            # Set up database optimizations
            logger.info("Setting up database optimizations...")
            setup_script = """
import sys
sys.path.append('server')
from database_optimizer import setup_database_optimizations
setup_database_optimizations()
print("Database optimizations complete")
"""
            
            subprocess.run([sys.executable, '-c', setup_script], check=True, cwd=self.project_root)
            
            logger.info("✅ Database setup complete")
            return True
            
        except Exception as e:
            logger.error(f"❌ Database setup failed: {e}")
            return False
    
    def create_directories(self):
        """Create necessary directories"""
        logger.info("📁 Creating directories...")
        
        directories = [
            'backups/database',
            'backups/files', 
            'backups/logs',
            'server/logs',
            'server/sessions',
            'logs'
        ]
        
        for directory in directories:
            dir_path = self.project_root / directory
            dir_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created directory: {directory}")
        
        logger.info("✅ Directories created")
        return True
    
    def setup_security(self):
        """Set up security configurations"""
        logger.info("🔒 Setting up security...")
        
        try:
            # Generate secure keys if not provided
            if not os.getenv('JWT_SECRET_KEY'):
                import secrets
                jwt_key = secrets.token_urlsafe(32)
                logger.warning(f"Generated JWT_SECRET_KEY: {jwt_key}")
                logger.warning("Please add this to your .env file: JWT_SECRET_KEY=" + jwt_key)
            
            if not os.getenv('ENCRYPTION_KEY'):
                from cryptography.fernet import Fernet
                encryption_key = Fernet.generate_key().decode()
                logger.warning(f"Generated ENCRYPTION_KEY: {encryption_key}")
                logger.warning("Please add this to your .env file: ENCRYPTION_KEY=" + encryption_key)
            
            # Run security audit
            audit_script = """
import sys
sys.path.append('server')
from security_manager import security_manager
audit = security_manager.run_security_audit()
print("Security audit complete:")
for check, result in audit['checks'].items():
    print(f"  {check}: {result['status']}")
"""
            
            subprocess.run([sys.executable, '-c', audit_script], check=True, cwd=self.project_root)
            
            logger.info("✅ Security setup complete")
            return True
            
        except Exception as e:
            logger.error(f"❌ Security setup failed: {e}")
            return False
    
    def create_systemd_service(self):
        """Create systemd service file for production deployment"""
        logger.info("⚙️ Creating systemd service...")
        
        service_content = f"""[Unit]
Description=Job Application Agent API Server
After=network.target postgresql.service redis.service

[Service]
Type=simple
User=www-data
WorkingDirectory={self.project_root}
Environment=PATH={self.project_root}/venv/bin
Environment=FLASK_ENV=production
EnvironmentFile={self.project_root}/.env
ExecStart={sys.executable} server/api_server.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""
        
        service_file = self.project_root / 'job-agent.service'
        with open(service_file, 'w') as f:
            f.write(service_content)
        
        logger.info(f"✅ Systemd service created: {service_file}")
        logger.info("To install: sudo cp job-agent.service /etc/systemd/system/")
        logger.info("To enable: sudo systemctl enable job-agent")
        logger.info("To start: sudo systemctl start job-agent")
        
        return True
    
    def create_nginx_config(self):
        """Create nginx configuration"""
        logger.info("🌐 Creating nginx configuration...")
        
        nginx_config = """server {
    listen 80;
    server_name your-domain.com;  # Replace with your domain
    
    # Redirect HTTP to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;  # Replace with your domain
    
    # SSL Configuration
    ssl_certificate /path/to/your/certificate.crt;
    ssl_certificate_key /path/to/your/private.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512:ECDHE-RSA-AES256-GCM-SHA384:DHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    
    # Security Headers
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    
    # Rate Limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
    limit_req zone=api burst=20 nodelay;
    
    # Proxy to Flask app
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
    
    # Static files (if any)
    location /static/ {
        alias /path/to/your/static/files/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
"""
        
        nginx_file = self.project_root / 'nginx-job-agent.conf'
        with open(nginx_file, 'w') as f:
            f.write(nginx_config)
        
        logger.info(f"✅ Nginx config created: {nginx_file}")
        logger.info("To install: sudo cp nginx-job-agent.conf /etc/nginx/sites-available/")
        logger.info("To enable: sudo ln -s /etc/nginx/sites-available/nginx-job-agent.conf /etc/nginx/sites-enabled/")
        logger.info("Remember to update server_name and SSL certificate paths")
        
        return True
    
    def run_tests(self):
        """Run basic system tests"""
        logger.info("🧪 Running system tests...")
        
        try:
            # Test database connection
            test_script = """
import sys
sys.path.append('server')
from database_optimizer import get_optimized_db_session

with get_optimized_db_session() as session:
    result = session.execute('SELECT 1')
    print("Database test: PASS")

# Test Redis connection
import redis
r = redis.Redis()
r.ping()
print("Redis test: PASS")

# Test job queue
from job_queue import job_queue
stats = job_queue.get_queue_stats()
print(f"Job queue test: PASS (queue_size: {stats['queue_size']})")

print("All tests passed!")
"""
            
            subprocess.run([sys.executable, '-c', test_script], check=True, cwd=self.project_root)
            
            logger.info("✅ All tests passed")
            return True
            
        except Exception as e:
            logger.error(f"❌ Tests failed: {e}")
            return False
    
    def deploy(self):
        """Run full deployment process"""
        logger.info("🚀 Starting production deployment...")
        
        steps = [
            ("Prerequisites", self.check_prerequisites),
            ("Dependencies", self.install_dependencies),
            ("Directories", self.create_directories),
            ("Database", self.setup_database),
            ("Security", self.setup_security),
            ("Systemd Service", self.create_systemd_service),
            ("Nginx Config", self.create_nginx_config),
            ("Tests", self.run_tests)
        ]
        
        for step_name, step_func in steps:
            logger.info(f"\n{'='*50}")
            logger.info(f"STEP: {step_name}")
            logger.info(f"{'='*50}")
            
            try:
                if not step_func():
                    logger.error(f"❌ Step '{step_name}' failed")
                    return False
            except Exception as e:
                logger.error(f"❌ Step '{step_name}' failed with exception: {e}")
                return False
        
        logger.info("\n" + "="*50)
        logger.info("🎉 DEPLOYMENT COMPLETE!")
        logger.info("="*50)
        
        logger.info("\nNext steps:")
        logger.info("1. Review and update .env file with any missing variables")
        logger.info("2. Install systemd service: sudo cp job-agent.service /etc/systemd/system/")
        logger.info("3. Configure nginx: sudo cp nginx-job-agent.conf /etc/nginx/sites-available/")
        logger.info("4. Set up SSL certificates")
        logger.info("5. Start services: sudo systemctl start job-agent")
        logger.info("6. Monitor logs: tail -f server/logs/api_server.log")
        
        return True

def main():
    """Main deployment function"""
    deployer = ProductionDeployer()
    
    try:
        success = deployer.deploy()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("\n❌ Deployment cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Deployment failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
