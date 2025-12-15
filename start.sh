#!/bin/bash

# 1. Start Celery Worker (Handling BOTH queues)
# -A src.app.celery_app: Points to your app instance.
# -Q sentiment,embeddings: Tells this ONE worker to consume from BOTH queues.
# --concurrency=2: We have 2 vCPUs. This lets it run 1 sentiment task and 1 embedding task in parallel.
# --pool=prefork: Standard for Linux (Solo is for Windows/Debug).
IS_CELERY_WORKER=true celery -A src.app.celery_app worker -Q sentiment,embeddings --loglevel=info --concurrency=2 &

# 2. Start FastAPI (Foreground)
# Port 7860 is mandatory for Hugging Face Spaces.
uvicorn src.app.main:app --host 0.0.0.0 --port 7860