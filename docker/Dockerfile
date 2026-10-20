# One image for every stage; STAGE selects ocr, llm or render at run time.
# Build from the repo root: docker build -f docker/Dockerfile -t keda-docs:dev .
FROM python:3.12-slim

WORKDIR /app
COPY requirements-worker.txt .
RUN pip install --no-cache-dir -r requirements-worker.txt
COPY src/ src/

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "src.worker"]
