FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements_production.txt /app/requirements_production.txt
RUN pip install --no-cache-dir -r /app/requirements_production.txt

# Copy application source
COPY . /app

# Railway injects PORT; api_server.py reads it (defaults to 5000)
EXPOSE 5000

CMD ["python", "server/api_server.py"]
