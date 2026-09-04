FROM python:3.11-slim

# Evita que Python genere archivos .pyc y fuerza salida de logs al instante
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Instalar dependencias del sistema y CLI de Alpaca
RUN apt-get update && apt-get install -y \
    curl \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Instalar Alpaca CLI
RUN curl -sL https://alpaca.markets/cli/install.sh | bash

# Configurar el directorio de trabajo
WORKDIR /app

# Instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código del proyecto
COPY . .

# Comando por defecto (aunque docker-compose lo sobreescribirá)
CMD ["uvicorn", "dashboard:app", "--host", "0.0.0.0", "--port", "5001"]
