"""
VNC Socket.IO Handler

Handles real-time VNC streaming via WebSocket using Flask-SocketIO
"""

import logging
from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect
import subprocess

logger = logging.getLogger(__name__)


def setup_vnc_socketio(socketio: SocketIO):
    """
    Setup VNC WebSocket handlers with Flask-SocketIO
    
    Args:
        socketio: Flask-SocketIO instance
    """
    
    @socketio.on('connect', namespace='/vnc')
    def handle_vnc_connect():
        """Handle VNC WebSocket connection"""
        logger.info(f"🔌 VNC WebSocket client connected: {request.sid}")
        emit('connection_response', {'status': 'connected'})
    
    @socketio.on('disconnect', namespace='/vnc')
    def handle_vnc_disconnect():
        """Handle VNC WebSocket disconnection"""
        logger.info(f"🔌 VNC WebSocket client disconnected: {request.sid}")
    
    @socketio.on('join_session', namespace='/vnc')
    def handle_join_session(data):
        """
        Client joins a specific VNC session room
        
        Data:
        {
            "session_id": "uuid",
            "auth_token": "bearer-token"
        }
        """
        try:
            session_id = data.get('session_id')
            
            if not session_id:
                emit('error', {'message': 'session_id required'})
                return
            
            # TODO: Verify user is authorized for this session
            # For now, allow all connections (add auth later)
            
            # Join room for this session
            join_room(session_id)
            
            logger.info(f"👤 Client joined VNC session: {session_id}")
            
            emit('joined_session', {
                'session_id': session_id,
                'message': 'Connected to VNC session'
            })
            
        except Exception as e:
            logger.error(f"Error joining VNC session: {e}")
            emit('error', {'message': str(e)})
    
    @socketio.on('leave_session', namespace='/vnc')
    def handle_leave_session(data):
        """Client leaves VNC session room"""
        try:
            session_id = data.get('session_id')
            leave_room(session_id)
            
            logger.info(f"👋 Client left VNC session: {session_id}")
            
        except Exception as e:
            logger.error(f"Error leaving VNC session: {e}")
    
    @socketio.on('vnc_input', namespace='/vnc')
    def handle_vnc_input(data):
        """
        Handle input from client to forward to VNC session
        (For future interactive mode)
        
        Data:
        {
            "session_id": "uuid",
            "type": "mouse"|"keyboard",
            "event": {...}
        }
        """
        try:
            session_id = data.get('session_id')
            input_type = data.get('type')
            event = data.get('event')
            
            # TODO: Forward input to VNC session
            # This allows user to interact with browser from website
            
            logger.debug(f"📥 VNC input for {session_id}: {input_type}")
            
        except Exception as e:
            logger.error(f"Error handling VNC input: {e}")
    
    logger.info("✅ VNC Socket.IO handlers configured")
    
    return socketio

