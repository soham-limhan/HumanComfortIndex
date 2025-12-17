FROM python:3.11-slim

LABEL maintainer="Soham <noreply@example.com>"

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	FLASK_APP=main.py \
	FLASK_ENV=production \
	PORT=5000

WORKDIR /app

# Install system packages required by some scientific packages (matplotlib)
RUN apt-get update \
	&& apt-get install -y --no-install-recommends \
	   build-essential \
	   libfreetype6-dev \
	   libpng-dev \
	   pkg-config \
	&& rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
	&& pip install --no-cache-dir matplotlib gunicorn

# Copy application source
COPY . .

# Run as non-root user for better security
RUN useradd -m appuser \
	&& chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

# Use Gunicorn to serve the Flask app; main:app is the WSGI entrypoint
CMD ["gunicorn", "main:app", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4"]

