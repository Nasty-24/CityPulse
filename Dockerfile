# CityPulse: trip planning API and dashboard
#   docker build -t citypulse .
#   docker run -p 8000:8000 -v citypulse-cache:/data citypulse
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CITYPULSE_CACHE_DB=/data/cache.db

WORKDIR /app

# dependencies first, so code changes don't reinstall them
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn>=0.30" "httpx>=0.28" "babel>=2.16"

COPY citypulse ./citypulse
COPY static ./static
COPY main.py ./

# run as an unprivileged user; the cache lives on a volume so it survives restarts
RUN useradd --create-home --uid 10001 app && mkdir /data && chown app /data
USER app
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4)" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
