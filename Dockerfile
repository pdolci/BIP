FROM python:3.11-slim

# Dipendenze di sistema:
# - libgomp1: richiesta da faiss-cpu
# - default-libmysqlclient-dev, pkg-config: per PyMySQL/mysqlclient
# - curl: per healthcheck opzionale
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Installa le dipendenze Python prima del codice sorgente
# (sfrutta la cache Docker: rigenera solo se requirements.txt cambia)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia il codice sorgente
COPY . .

# Crea le cartelle di upload (server_setup.py le crea a runtime,
# ma averle già nell'image garantisce i permessi corretti)
RUN mkdir -p uploads/books uploads/covers

# Porta di Gunicorn
EXPOSE 8000

# Entrypoint: script che decide se avviare web o scheduler
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
