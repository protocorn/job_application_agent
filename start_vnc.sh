#!/bin/bash
#
# VNC-enabled Job Application Agent Startup Script
# Initializes virtual display, VNC server, and Flask application
#

set -e  # Exit on error

echo "=================================="
echo "🚀 Starting Job Application Agent"
echo "=================================="

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check if a port is in use
port_in_use() {
    netstat -tuln 2>/dev/null | grep -q ":$1 " || ss -tuln 2>/dev/null | grep -q ":$1 "
}

# Verify required commands
echo "📋 Checking dependencies..."
DEPS_OK=true

if ! command_exists Xvfb; then
    echo "❌ ERROR: Xvfb not installed"
    DEPS_OK=false
fi

if ! command_exists x11vnc; then
    echo "❌ ERROR: x11vnc not installed"
    DEPS_OK=false
fi

if ! command_exists python; then
    echo "❌ ERROR: Python not installed"
    DEPS_OK=false
fi

if [ "$DEPS_OK" = false ]; then
    echo "❌ Missing required dependencies"
    exit 1
fi

echo "✅ All dependencies found"

# Start Xvfb (Virtual Display)
echo ""
echo "🖥️  Starting Xvfb virtual display..."
export DISPLAY=:99

# Kill any existing Xvfb on display :99
if pgrep -f "Xvfb :99" > /dev/null; then
    echo "⚠️  Cleaning up existing Xvfb process..."
    pkill -f "Xvfb :99" || true
    sleep 1
fi

Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!

# Wait for Xvfb to start
sleep 3

if ! ps -p $XVFB_PID > /dev/null; then
    echo "❌ ERROR: Xvfb failed to start"
    exit 1
fi

echo "✅ Xvfb started (PID: $XVFB_PID, DISPLAY: $DISPLAY)"

# Start x11vnc (VNC Server)
echo ""
echo "📺 Starting x11vnc server..."

# Kill any existing x11vnc
if pgrep x11vnc > /dev/null; then
    echo "⚠️  Cleaning up existing x11vnc process..."
    pkill x11vnc || true
    sleep 1
fi

x11vnc -display :99 -nopw -forever -shared -rfbport 5900 -quiet &
VNC_PID=$!

# Wait for VNC to start and verify port is listening
sleep 3

if ! ps -p $VNC_PID > /dev/null; then
    echo "❌ ERROR: x11vnc failed to start"
    kill $XVFB_PID 2>/dev/null || true
    exit 1
fi

# Verify VNC port is listening
VNC_READY=false
for i in {1..10}; do
    if port_in_use 5900; then
        VNC_READY=true
        break
    fi
    echo "⏳ Waiting for VNC port 5900... (attempt $i/10)"
    sleep 1
done

if [ "$VNC_READY" = false ]; then
    echo "❌ ERROR: VNC port 5900 not listening after 10 seconds"
    kill $VNC_PID 2>/dev/null || true
    kill $XVFB_PID 2>/dev/null || true
    exit 1
fi

echo "✅ VNC server started (PID: $VNC_PID, Port: 5900)"

# Start Flask Application
echo ""
echo "🐍 Starting Flask application..."
echo "   Working directory: $(pwd)"
echo "   Python version: $(python --version)"
echo "   Port: ${PORT:-5000}"

cd /app

# Set Python to unbuffered mode for better logging
export PYTHONUNBUFFERED=1

# Trap signals to ensure cleanup
cleanup() {
    echo ""
    echo "🛑 Shutting down..."
    echo "   Stopping Flask..."
    kill $FLASK_PID 2>/dev/null || true
    echo "   Stopping VNC..."
    kill $VNC_PID 2>/dev/null || true
    echo "   Stopping Xvfb..."
    kill $XVFB_PID 2>/dev/null || true
    echo "✅ Cleanup complete"
    exit 0
}

trap cleanup SIGTERM SIGINT

# Start Flask application
python server/api_server.py &
FLASK_PID=$!

echo "✅ Flask started (PID: $FLASK_PID)"
echo ""
echo "=================================="
echo "✅ All services started successfully"
echo "=================================="
echo "📺 VNC: localhost:5900"
echo "🌐 API: 0.0.0.0:${PORT:-5000}"
echo "🏥 Health: http://localhost:${PORT:-5000}/health"
echo "=================================="

# Wait for Flask to exit (keeps container running)
wait $FLASK_PID
FLASK_EXIT_CODE=$?

echo ""
echo "⚠️  Flask exited with code $FLASK_EXIT_CODE"

# Cleanup
cleanup

