FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    wget gnupg curl \
    && rm -rf /var/lib/apt/lists/*

# Build context is repo root, so paths are relative to root
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium --with-deps

COPY backend/ .
COPY frontend/ ../frontend/

RUN chmod +x start.sh

EXPOSE 8000

CMD ["sh", "start.sh"]
