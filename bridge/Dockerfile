# Container image for the Birdie Cloud Bridge (e.g. Google Cloud Run).
# Configuration comes from environment variables / the platform's secret
# store; nothing secret is baked into the image.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BRIDGE_HOST=0.0.0.0

WORKDIR /app
COPY pyproject.toml README.md ./
COPY birdie_bridge ./birdie_bridge
RUN pip install --no-cache-dir . && useradd --system --uid 10001 bridge

USER bridge
EXPOSE 8080
CMD ["python", "-m", "birdie_bridge"]
