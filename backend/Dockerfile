# Use a slim Python image. dlib (used by face_recognition) requires cmake + build tools.
FROM python:3.10-slim

# Install system dependencies needed by dlib
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    libopenblas-dev \
    liblapack-dev \
    libx11-dev \
    libgtk-3-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for layer caching
COPY requirements.txt .

# Install Python dependencies (this includes compiling dlib - takes time on first build)
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Download the UltraFace ONNX model (not stored in git)
RUN python download_model.py

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
