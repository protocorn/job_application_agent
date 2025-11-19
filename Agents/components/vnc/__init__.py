"""
VNC (Virtual Network Computing) components for cloud browser automation

Enables running visible browsers in cloud environments and streaming
the display to users for interaction.
"""

from .virtual_display_manager import VirtualDisplayManager
from .vnc_server import VNCServer
from .browser_vnc_coordinator import BrowserVNCCoordinator, BrowserVNCSession
from .vnc_session_manager import VNCSessionManager, vnc_session_manager

__all__ = [
    'VirtualDisplayManager',
    'VNCServer',
    'BrowserVNCCoordinator',
    'BrowserVNCSession',
    'VNCSessionManager',
    'vnc_session_manager'
]

