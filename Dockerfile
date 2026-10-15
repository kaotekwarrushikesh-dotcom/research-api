# Runs unchanged on Hugging Face Spaces and on Cloud Run.
#
# The only difference between the two is the port: Spaces expects 7860 and injects nothing,
# Cloud Run injects PORT=8080. Defaulting PORT to 7860 and honouring the variable satisfies
# both, so there is one image rather than two that drift apart.

# ---------- builder ----------
# The compilers needed to install scipy, statsmodels and arch stay here and never reach the
# runtime image, because image size is most of a cold start on either platform.
FROM python:3.11-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---------- runtime ----------
FROM python:3.11-slim
COPY --from=builder /install /usr/local

# Spaces runs containers as uid 1000, and anything the app writes must be owned by that user.
# Running as root here works locally and then fails on Spaces with a permission error, which
# is a slow way to find out.
RUN useradd -m -u 1000 user
USER user

ENV HOME=/home/user \
    XDG_CACHE_HOME=/home/user/.cache \
    PYTHONUNBUFFERED=1 \
    PORT=7860

WORKDIR $HOME/app
COPY --chown=user:user app ./app

EXPOSE 7860

# One worker deliberately. The response cache lives in process memory, so a second worker
# would hold its own copy and double the upstream fetches for no benefit.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1
