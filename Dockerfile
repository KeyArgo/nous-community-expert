FROM python:3.13-slim

WORKDIR /app

COPY serve.py .
COPY index.html .
COPY favicon.svg .
# Generated JSON outputs (sharded to stay under CF Pages 25 MiB limit)
COPY metadata.json ./
COPY search-data-*.json ./
COPY search-index-*.json ./

EXPOSE 8080

CMD ["python", "serve.py", "8080"]
