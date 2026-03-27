# Use official Playwright Python image — Chromium is pre-installed
FROM mcr.microsoft.com/playwright/python:v1.50.0-jammy

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (chromium already in base image, this ensures binaries are linked)
RUN playwright install chromium

# Copy application code
COPY . .

# Use shell form so Railway's $PORT env var is expanded at runtime
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
