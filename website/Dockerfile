# The AT SUITE website, shop, customer portal and admin.
#   docker build -t atsuite-shop website/
# Settings come from the environment (.env.example). The licence signing key is mounted as a
# secret file, never built into the image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SHOP_DATA=/data
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 shop && mkdir /data && chown shop /data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY catalogue.toml ./
COPY atshop ./atshop
COPY tools ./tools

USER shop
VOLUME /data
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/api/health', timeout=4)"
CMD ["uvicorn", "atshop.app:app", "--host", "0.0.0.0", "--port", "8090", "--proxy-headers", "--forwarded-allow-ips", "*"]
