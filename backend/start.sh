#!/bin/sh
# Start API server in background
python main.py &

# Start Telegram bot (only if token is set)
if [ -n "$TELEGRAM_BOT_TOKEN" ]; then
  echo "Starting Telegram bot..."
  python telegram_bot.py &
else
  echo "TELEGRAM_BOT_TOKEN not set, skipping bot"
fi

# Wait for any process to exit
wait -n

# Exit with status of process that exited first
exit $?
