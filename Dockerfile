# Usamos una imagen de Python oficial
FROM python:3.11-slim

# Instalamos FFmpeg y herramientas necesarias del sistema
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiamos e instalamos tus requisitos (incluye imageio-ffmpeg y pyrogram)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos todo el código (main.py, userbot_drive.py, etc.)
COPY . .

# Por defecto no ponemos CMD aquí porque lo definiremos en el docker-compose
