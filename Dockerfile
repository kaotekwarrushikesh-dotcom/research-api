# Two stages: the compilers needed to install scipy, statsmodels and arch stay in the builder
# and never reach the deployed image, because image size is most of a Cloud Run cold start.
FROM python:3.11-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim
COPY --from=builder /install /usr/local
WORKDIR /srv
COPY app ./app

# Several libraries write caches at runtime; pointing HOME at a writable path avoids the
# PermissionError class of failure that broke an earlier deployment of these engines.
ENV PYTHONUNBUFFERED=1 HOME=/tmp XDG_CACHE_HOME=/tmp/.cache PORT=8080
EXPOSE 8080

# One worker on purpose. The response cache lives in process memory, so a second worker would
# hold a second copy and double the upstream fetches. Scale with Cloud Run instances instead.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1
