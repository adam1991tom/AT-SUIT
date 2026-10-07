# AT-SUIT server image.
#   docker build -t atsuit .                       (with speech recognition)
#   docker build --build-arg WITH_ASR=0 -t atsuit .   (smaller, no captions)
FROM python:3.12-slim

ARG WITH_ASR=1
ARG VERSION=dev
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ATSUIT_DATA=/data ATSUIT_PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends openssh-client bzip2 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r atsuit && useradd -r -g atsuit -d /app atsuit

WORKDIR /app
COPY server/requirements.txt server/requirements-asr.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$WITH_ASR" = "1" ]; then pip install --no-cache-dir -r requirements-asr.txt; fi

COPY server/atsuit ./atsuit
COPY node-agent/atsuit_node.py ./atsuit/agent/atsuit_node.py
COPY screen-agent/atsuit_screen.py screen-agent/install.sh ./atsuit/agent/screen/
RUN mkdir -p /data && chown -R atsuit:atsuit /data /app
LABEL org.opencontainers.image.title="AT-SUIT" org.opencontainers.image.version="${VERSION}"

USER atsuit
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=3)" || exit 1
CMD ["sh", "-c", "exec uvicorn atsuit.main:app --host 0.0.0.0 --port ${ATSUIT_PORT} --proxy-headers --forwarded-allow-ips='*' --ws-max-size 1048576"]
