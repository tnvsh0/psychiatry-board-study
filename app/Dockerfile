FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir streamlit requests

COPY app.py .

# schedule + symbols are fetched from GitHub raw on each cold-start
# so we don't bake them into the image

EXPOSE 8080
CMD ["streamlit", "run", "app.py", "--server.port", "8080", "--server.address", "0.0.0.0", "--server.headless", "true"]
