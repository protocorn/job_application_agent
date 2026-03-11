FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements-cli.txt /app/requirements-cli.txt
COPY requirements-server.txt /app/requirements-server.txt
RUN pip install --no-cache-dir -r /app/requirements-cli.txt -r /app/requirements-server.txt

# Copy application source
COPY . /app

# Railway injects PORT; api_server.py reads it (defaults to 5000)
EXPOSE 5000

CMD ["python", "server/api_server.py"]
