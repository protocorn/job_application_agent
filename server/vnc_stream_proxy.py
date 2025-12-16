"""
VNC Stream WebSocket Proxy Route

Provides a WebSocket endpoint at /vnc-stream/<session_id> that proxies
VNC traffic from the frontend to the backend VNC server via websockify.

This uses Flask-Sock to create raw WebSocket connections that proxy to websockify.
"""

import logging
from flask import Blueprint
from typing import Dict, Optional
import socket
import threading
import struct
import time

logger = logging.getLogger(__name__)

# Configuration constants
SOCKET_BUFFER_SIZE = 16384  # Increased buffer for better throughput
SOCKET_TIMEOUT = 30.0  # Socket timeout in seconds
MAX_RETRY_ATTEMPTS = 3  # Max retries for VNC connection
RETRY_DELAY = 2.0  # Delay between retries in seconds

# Track active VNC sessions and their ports
# session_id -> {'vnc_port': int, 'ws_port': int}
vnc_session_ports: Dict[str, Dict[str, int]] = {}

def register_vnc_session(session_id: str, vnc_port: int, ws_port: int):
    """Register a VNC session with its ports for WebSocket proxying"""
    vnc_session_ports[session_id] = {
        'vnc_port': vnc_port,
        'ws_port': ws_port
    }
    logger.info(f"📝 Registered VNC session {session_id}: VNC port {vnc_port}, WS port {ws_port}")

def unregister_vnc_session(session_id: str):
    """Unregister a VNC session"""
    if session_id in vnc_session_ports:
        del vnc_session_ports[session_id]
        logger.info(f"🗑️ Unregistered VNC session {session_id}")

def get_vnc_session_port(session_id: str) -> Optional[Dict[str, int]]:
    """Get VNC and WebSocket ports for a session"""
    return vnc_session_ports.get(session_id)


def setup_vnc_websocket_routes(app):
    """
    Setup WebSocket proxy routes for VNC streaming using Flask-Sock

    This creates a /vnc-stream/<session_id> endpoint that proxies
    WebSocket connections to the local websockify instance.
    """
    try:
        from flask_sock import Sock

        sock = Sock(app)

        @sock.route('/vnc-stream/<session_id>')
        def vnc_stream(ws, session_id):
            """
            WebSocket route that proxies VNC traffic to websockify

            Args:
                ws: WebSocket connection from client
                session_id: VNC session identifier
            """
            logger.info(f"🔌 New VNC WebSocket connection for session: {session_id}")
            logger.info(f"🔍 DEBUG - Active sessions in registry: {list(vnc_session_ports.keys())}")

            # Get the VNC port for this session
            session_ports = get_vnc_session_port(session_id)

            if not session_ports:
                logger.error(f"❌ No VNC session found for: {session_id}")
                logger.error(f"🔍 DEBUG - Available sessions: {vnc_session_ports}")
                try:
                    ws.close(1008, "Session not found")  # 1008 = Policy Violation
                except:
                    pass
                return

            # CRITICAL FIX: Connect directly to VNC server (TCP), not websockify
            # The frontend connects to this Flask-Sock endpoint via WebSocket.
            # We must unwrap the WebSocket frames and forward raw TCP to the VNC server.
            # Connecting to websockify (6900) would fail because it expects a WebSocket handshake.
            vnc_port = session_ports['vnc_port']
            ws_port = session_ports['ws_port']
            logger.info(f"📡 Proxying directly to VNC server on localhost:{vnc_port}")
            logger.info(f"🔍 DEBUG - Session {session_id} -> VNC port {vnc_port}, WS port {ws_port}")

            # Connect to local VNC server with retry logic
            vnc_socket = None
            last_error = None
            
            for attempt in range(MAX_RETRY_ATTEMPTS):
                try:
                    vnc_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    
                    # Set socket options for better stability and performance
                    vnc_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                    vnc_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    vnc_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER_SIZE)
                    vnc_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUFFER_SIZE)
                    vnc_socket.settimeout(SOCKET_TIMEOUT)
                    
                    # Connect to VNC server
                    vnc_socket.connect(('localhost', vnc_port))
                    logger.info(f"✅ Connected to VNC server for session {session_id} (attempt {attempt + 1})")
                    break  # Success!
                    
                except (ConnectionRefusedError, OSError, socket.timeout) as e:
                    last_error = e
                    logger.warning(f"⚠️ Connection attempt {attempt + 1}/{MAX_RETRY_ATTEMPTS} failed: {e}")
                    
                    if vnc_socket:
                        try:
                            vnc_socket.close()
                        except:
                            pass
                        vnc_socket = None
                    
                    if attempt < MAX_RETRY_ATTEMPTS - 1:
                        time.sleep(RETRY_DELAY)
                    else:
                        logger.error(f"❌ Failed to connect to VNC server after {MAX_RETRY_ATTEMPTS} attempts")
                        try:
                            ws.close(1011, "VNC server not available")
                        except:
                            pass
                        return
            
            if not vnc_socket:
                logger.error(f"❌ Could not establish VNC connection for session {session_id}")
                try:
                    ws.close(1011, str(last_error))
                except:
                    pass
                return

            # Connection successful - start proxying with proper error handling
            try:
                # Shared state for coordinating shutdown
                shutdown_event = threading.Event()
                
                def forward_to_vnc():
                    """Forward data from client to VNC with error handling"""
                    try:
                        while not shutdown_event.is_set():
                            try:
                                data = ws.receive()
                                if data is None:
                                    logger.debug(f"Session {session_id}: Client closed connection")
                                    break
                                if isinstance(data, str):
                                    data = data.encode()
                                vnc_socket.sendall(data)
                            except Exception as e:
                                if not shutdown_event.is_set():
                                    logger.warning(f"Session {session_id}: Error receiving from client: {e}")
                                break
                    except Exception as e:
                        logger.warning(f"Session {session_id}: Client → VNC forwarding ended: {e}")
                    finally:
                        shutdown_event.set()  # Signal the other thread to stop
                        try:
                            vnc_socket.shutdown(socket.SHUT_WR)
                        except:
                            pass

                def forward_to_client():
                    """Forward data from VNC to client with error handling"""
                    try:
                        while not shutdown_event.is_set():
                            try:
                                vnc_socket.settimeout(1.0)  # Check shutdown_event periodically
                                data = vnc_socket.recv(SOCKET_BUFFER_SIZE)
                                if not data:
                                    logger.debug(f"Session {session_id}: VNC server closed connection")
                                    break
                                # Send binary data explicitly
                                ws.send(bytes(data))
                            except socket.timeout:
                                continue  # Check shutdown_event and retry
                            except Exception as e:
                                if not shutdown_event.is_set():
                                    logger.warning(f"Session {session_id}: Error sending to client: {e}")
                                break
                    except Exception as e:
                        logger.warning(f"Session {session_id}: VNC → Client forwarding ended: {e}")
                    finally:
                        shutdown_event.set()  # Signal the other thread to stop
                        try:
                            ws.close()
                        except:
                            pass

                # Start forwarding threads
                client_thread = threading.Thread(target=forward_to_vnc, name=f"VNC-Client-{session_id}", daemon=True)
                vnc_thread = threading.Thread(target=forward_to_client, name=f"VNC-Server-{session_id}", daemon=True)

                client_thread.start()
                vnc_thread.start()

                # Wait for threads to complete with timeout
                client_thread.join(timeout=300)  # 5 minute max
                vnc_thread.join(timeout=5)  # Quick cleanup

                logger.info(f"🔌 VNC WebSocket closed for session {session_id}")

            except Exception as e:
                logger.error(f"❌ Error in VNC proxy: {e}")
                import traceback
                logger.error(traceback.format_exc())
            finally:
                # Ensure cleanup
                try:
                    vnc_socket.close()
                except:
                    pass
                try:
                    ws.close()
                except:
                    pass

        logger.info("✅ VNC WebSocket proxy routes registered (Flask-Sock)")
        return True

    except ImportError:
        logger.warning("⚠️ Flask-Sock not installed - VNC WebSocket proxy unavailable")
        logger.info("   Install with: pip install flask-sock")
        return False
