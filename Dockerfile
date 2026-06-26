# Macgear 3PL Portal — production image (droplet).
# The app NEVER calls NetSuite; it only serves the portal + token-authed ingest/billing endpoints.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# Deps first for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code.
COPY app ./app
COPY db ./db
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8000
# Entrypoint seeds (idempotent, SEED_DEMO from env) then launches uvicorn.
ENTRYPOINT ["./entrypoint.sh"]
