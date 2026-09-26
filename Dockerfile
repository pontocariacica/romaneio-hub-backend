FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-por \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000 MAX_MB=50 MAX_PAGES=1500 OCR_ENABLED=true TIMEOUT_SECONDS=120 ALLOWED_ORIGINS=*
EXPOSE 8000
RUN useradd -m appuser
USER appuser
CMD ["sh","-c","uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
