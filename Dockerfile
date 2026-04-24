FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Create persistent directories
RUN mkdir -p data logs

# Expose volumes for DuckDB persistence and logs
VOLUME ["/app/data", "/app/logs"]

# Health check: confirm the bot process is running
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD pgrep -f "python bot.py" || exit 1

CMD ["python", "bot.py"]
