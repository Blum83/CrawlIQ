#!/bin/sh
# Start API server in background
echo "Starting API server..."
python main.py &
API_PID=$!

# Wait for API to be ready before starting bot
echo "Waiting for API to start..."
sleep 5

# Start Telegram bot (only if token is set)
if [ -n "$TELEGRAM_BOT_TOKEN" ]; then
  echo "Starting Telegram bot..."
  python telegram_bot.py &
else
  echo "TELEGRAM_BOT_TOKEN not set, skipping bot"
fi

# Wait for API server (main process)
wait $API_PID
