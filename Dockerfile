FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY config ./config

# Runs the pipeline once and exits. Schedule with cron / `docker compose run`.
CMD ["python", "-m", "src.main"]
