FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies (needed for shapely/geopandas/opencv if any)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Expose port (Hugging Face Spaces expects 7860, Render/Koyeb read PORT env)
EXPOSE 7860

# Start command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
