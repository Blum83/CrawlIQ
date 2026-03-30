#!/bin/sh
echo "Starting API server..."
PORT=${PORT:-8000}
exec python main.py
