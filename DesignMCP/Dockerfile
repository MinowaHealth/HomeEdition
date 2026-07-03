ARG PYTHON_VERSION=3.13-slim
FROM python:${PYTHON_VERSION}

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 33282

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:33282/health || exit 1

CMD ["python", "mcp_server.py"]
