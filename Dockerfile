# krypt-agent tennis — Dockerfile for Railway deployment
# Single stage, python:3.13-slim. The bot only needs `requests`, so the
# image is tiny (~50 MB).

FROM python:3.13-slim

# System locale: Railway containers default to ASCII, which breaks our
# logger on accented player names. Force UTF-8 end-to-end.
ENV PYTHONIOENCODING=utf-8 \
    PYTHONUTF8=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install Python deps first (better Docker-layer caching: requirements.txt
# changes infrequently, source changes often).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source. .dockerignore narrows this down so we don't ship logs/venv.
COPY . .

# Railway assigns a public port at runtime via $PORT, which our launcher
# accepts via --port. Default to 8888 so this runs locally too.
ENV PORT=8888

# Expose for documentation; Railway maps it automatically.
EXPOSE 8888

# Long-running foreground process. Railway restarts on crash.
# --minutes 100000 = effectively "forever" (~69 days per run, then restart).
# --hard-cap 1.0 is the CLI default; V12/V13 override to 0.50/0.35 internally.
CMD ["sh", "-c", "python -u tennis_multi_v9.py --minutes 100000 --poll 5 --stake 10 --port ${PORT:-8888} --max-session-loss 25 --hard-cap 1.0"]
