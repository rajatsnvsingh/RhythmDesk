FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/srv/media/music/curator-state/cache
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/rhythm-curator
LABEL org.opencontainers.image.source="https://github.com/rajatsnvsingh/RhythmDesk" \
      org.opencontainers.image.description="A self-hosted, approval-first music curation desk" \
      org.opencontainers.image.licenses="MIT"
COPY LICENSE ./LICENSE
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY config/config.yaml /etc/rhythm-curator/config.yaml
ENV BEETS_CONFIG=/etc/rhythm-curator/config.yaml
USER 10001:10000
CMD ["python", "app/worker.py"]
