# Cloud Run image for the Streamlit course site.
# Build context = repo ROOT (so we can pull both app/app.py and scripts/sr_engine.py).
#   gcloud run deploy psychiatry-course --source . --region me-west1 \
#     --set-env-vars GH_REPO=tnvsh0/psychiatry-board-study,GH_TOKEN=<token> --allow-unauthenticated
FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir streamlit requests

# app.py imports sr_engine; put them side by side so the bare import resolves.
COPY app/app.py .
COPY scripts/sr_engine.py .

# schedule + symbols + per-day artifacts are fetched live from the (private)
# GitHub repo via the Contents API using GH_TOKEN — not baked into the image.

EXPOSE 8080
CMD ["streamlit", "run", "app.py", "--server.port", "8080", "--server.address", "0.0.0.0", "--server.headless", "true"]
