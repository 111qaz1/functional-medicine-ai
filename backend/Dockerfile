FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_WAIT_POLICY=PASSIVE \
    TOKENIZERS_PARALLELISM=false \
    HF_HOME=/app/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/app/.cache/sentence-transformers \
    FM_RAG_ENABLED=1 \
    FM_RAG_INDEX_DIR=/app/backend/app/data/rag_index \
    FM_RAG_LOCAL_FILES_ONLY=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.7.1+cpu
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

ARG PRELOAD_RAG_MODEL=0
RUN if [ "$PRELOAD_RAG_MODEL" = "1" ]; then \
      HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3')" ; \
    else \
      echo "Skipping BAAI/bge-m3 preload; set PRELOAD_RAG_MODEL=1 for release images." ; \
    fi

COPY backend/app /app/backend/app

WORKDIR /app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).read()" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
