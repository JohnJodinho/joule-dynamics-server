FROM python:3.11-slim

# Create a non-root user with UID 1000 as required by HF Spaces
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python dependencies
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Copy all application files into the container
COPY --chown=user . .

# Expose the mandatory Hugging Face port
EXPOSE 7860

# Run FastAPI directly in the foreground
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]