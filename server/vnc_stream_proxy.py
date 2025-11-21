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

logger = logging.getLogger(__name__)

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

            # Get the websockify port for this session
            session_ports = get_vnc_session_port(session_id)

            if not session_ports:
                logger.error(f"❌ No VNC session found for: {session_id}")
                try:
                    ws.close(1008, "Session not found")  # 1008 = Policy Violation
                except:
                    pass
                return

            ws_port = session_ports['ws_port']
            logger.info(f"📡 Proxying to websockify on localhost:{ws_port}")

            # Connect to local websockify
            try:
                vnc_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                vnc_socket.connect(('localhost', ws_port))
                logger.info(f"✅ Connected to websockify for session {session_id}")

                # Proxy data between client WebSocket and websockify
                def forward_to_vnc():
                    """Forward data from client to VNC"""
                    try:
                        while True:
                            data = ws.receive()
                            if data is None:
                                break
                            if isinstance(data, str):
                                data = data.encode()
                            vnc_socket.sendall(data)
                    except Exception as e:
                        logger.debug(f"Client → VNC forwarding ended: {e}")
                    finally:
                        try:
                            vnc_socket.close()
                        except:
                            pass

                def forward_to_client():
                    """Forward data from VNC to client"""
                    try:
                        while True:
                            data = vnc_socket.recv(4096)
                            if not data:
                                break
                            ws.send(data)
                    except Exception as e:
                        logger.debug(f"VNC → Client forwarding ended: {e}")
                    finally:
                        try:
                            ws.close()
                        except:
                            pass

                # Start forwarding threads
                client_thread = threading.Thread(target=forward_to_vnc, daemon=True)
                vnc_thread = threading.Thread(target=forward_to_client, daemon=True)

                client_thread.start()
                vnc_thread.start()

                # Wait for threads to complete
                client_thread.join()
                vnc_thread.join()

                logger.info(f"🔌 VNC WebSocket closed for session {session_id}")

            except ConnectionRefusedError:
                logger.error(f"❌ Could not connect to websockify on port {ws_port}")
                try:
                    ws.close(1011, "Websockify not available")  # Use numeric code instead of reason kwarg
                except:
                    pass
            except Exception as e:
                logger.error(f"❌ Error in VNC proxy: {e}")
                try:
                    ws.close(1011, str(e))  # Use numeric code instead of reason kwarg
                except:
                    pass

        logger.info("✅ VNC WebSocket proxy routes registered (Flask-Sock)")
        return True

    except ImportError:
        logger.warning("⚠️ Flask-Sock not installed - VNC WebSocket proxy unavailable")
        logger.info("   Install with: pip install flask-sock")
        return False
