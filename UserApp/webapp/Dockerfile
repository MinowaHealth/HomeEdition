# Dockerfile for Minowa.ai healthv10 Web Application
#
# PYTHON_VERSION default is 3.13-slim for production. Override at build
# time for testing-role / experimentation:
#   docker build --build-arg PYTHON_VERSION=3.14-slim ...

ARG PYTHON_VERSION=3.13-slim
FROM python:${PYTHON_VERSION}

# Git short SHA of the build, surfaced at /api/v1/healthz as `version`.
# Pass at build time: docker build --build-arg APP_VERSION=$(git rev-parse --short HEAD) ...
# Falls back to "unknown" if unset, so callers can tell a build wasn't tagged.
ARG APP_VERSION=unknown
ENV APP_VERSION=${APP_VERSION}

WORKDIR /app

# Install system dependencies.
# tesseract-ocr + poppler-utils + libmagic1 power the in-process document
# OCR pipeline (Home Edition runs Tesseract inline on upload — no OCRWorker).
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    tesseract-ocr \
    poppler-utils \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY *.py ./
COPY *.html ./
COPY templates/ ./templates/
COPY routes/ ./routes/
COPY ocr/ ./ocr/
COPY object_store/ ./object_store/
COPY shared/ ./shared/

# Create logs directory
RUN mkdir -p /app/logs

# Expose port
EXPOSE 80

# Run with gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:80", "--workers", "4", "--threads", "2", "--timeout", "120", "app:app"]
