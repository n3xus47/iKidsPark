FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    IKIDS_HTTP=1 \
    PORT=8080

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY logo.png logox221.png pwalogo.png 14.svg ./
COPY assets ./assets
COPY supabase ./supabase

EXPOSE 8080

CMD ["python", "main.py"]
