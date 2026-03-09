#!/bin/sh
# Start API server in background
python main.py &
API_PID=$!

# Start Telegram bot (only if token is set)
if [ -n "$TELEGRAM_BOT_TOKEN" ]; then
  echo "Starting Telegram bot..."
  python telegram_bot.py &
fi

# Wait for API server (main process)
wait $API_PID
