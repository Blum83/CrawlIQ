#!/bin/sh
# Start API server in background
echo "Starting API server..."
python main.py &
API_PID=$!

# Wait for API to actually respond (health check loop)
echo "Waiting for API to be ready..."
PORT=${PORT:-8000}
TRIES=0
until python -c "import urllib.request; urllib.request.urlopen('http://localhost:$PORT/api/health')" 2>/dev/null; do
  TRIES=$((TRIES + 1))
  if [ $TRIES -ge 30 ]; then
    echo "API did not start in time, proceeding anyway..."
    break
  fi
  sleep 2
done
echo "API is ready on port $PORT"

# Start Telegram bot (only if token is set)
if [ -n "$TELEGRAM_BOT_TOKEN" ]; then
  echo "Starting Telegram bot..."
  python telegram_bot.py &
else
  echo "TELEGRAM_BOT_TOKEN not set, skipping bot"
fi

# Wait for API server (main process)
wait $API_PID
