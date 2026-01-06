"""
VNC Health Check Utility

Provides utilities to verify VNC server is actually listening and accepting connections.
"""

import socket
import time
import logging
from typing import Tuple

logger = logging.getLogger(__name__)


def check_port_listening(host: str, port: int, timeout: float = 2.0) -> bool:
    """
    Check if a port is listening and accepting connections
    
    Args:
        host: Hostname or IP address
        port: Port number
        timeout: Connection timeout in seconds
        
    Returns:
        True if port is listening and accepting connections
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception as e:
        logger.debug(f"Port check failed for {host}:{port}: {e}")
        return False


def wait_for_port(host: str, port: int, timeout: float = 10.0, check_interval: float = 0.5) -> bool:
    """
    Wait for a port to start listening
    
    Args:
        host: Hostname or IP address
        port: Port number
        timeout: Maximum time to wait in seconds
        check_interval: Time between checks in seconds
        
    Returns:
        True if port became available within timeout
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        if check_port_listening(host, port, timeout=2.0):
            return True
        time.sleep(check_interval)
    return False


def verify_vnc_server(host: str, port: int, timeout: float = 5.0) -> Tuple[bool, str]:
    """
    Verify VNC server is running and responding
    
    Args:
        host: VNC server hostname
        port: VNC server port
        timeout: Connection timeout in seconds
        
    Returns:
        Tuple of (success, message)
    """
    try:
        # Try to connect and receive VNC protocol version
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        
        # Connect
        sock.connect((host, port))
        
        # VNC server should send protocol version immediately
        # e.g., "RFB 003.008\n"
        try:
            data = sock.recv(12)
            if data and data.startswith(b'RFB'):
                sock.close()
                return True, f"VNC server responding with {data.decode().strip()}"
            else:
                sock.close()
                return False, f"VNC server not responding with valid protocol (got: {data})"
        except socket.timeout:
            sock.close()
            return False, "VNC server connected but not responding"
            
    except ConnectionRefusedError:
        return False, f"Connection refused to {host}:{port}"
    except socket.timeout:
        return False, f"Connection timeout to {host}:{port}"
    except Exception as e:
        return False, f"VNC verification failed: {e}"


def get_vnc_health_status(host: str, port: int) -> dict:
    """
    Get comprehensive health status of VNC server
    
    Args:
        host: VNC server hostname
        port: VNC server port
        
    Returns:
        Dict with health status information
    """
    status = {
        'port': port,
        'host': host,
        'listening': False,
        'responding': False,
        'healthy': False,
        'message': ''
    }
    
    # Check if port is listening
    if check_port_listening(host, port):
        status['listening'] = True
        
        # Verify VNC protocol
        success, message = verify_vnc_server(host, port)
        status['responding'] = success
        status['message'] = message
        status['healthy'] = success
    else:
        status['message'] = f"Port {port} not listening"
    
    return status

