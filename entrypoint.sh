#!/bin/sh
set -e

# Eseguito da ENTRYPOINT — decide se avviare la web app o lo scheduler.
# La variabile ROLE è impostata nel docker-compose.yml:
#   ROLE=web       → esegue setup + Gunicorn
#   ROLE=scheduler → esegue solo lo scheduler worker

echo "==> Avvio BIP con ruolo: ${ROLE:-web}"

if [ "${ROLE}" = "scheduler" ]; then
    echo "==> Avvio scheduler worker..."
    exec python scheduler_worker.py
else
    # Ruolo web (default): esegui il setup prima di Gunicorn
    echo "==> Esecuzione server_setup.py (migrazioni + cartelle)..."
    python server_setup.py

    echo "==> Avvio Gunicorn..."
    exec gunicorn \
        --bind 0.0.0.0:8000 \
        --workers 4 \
        --timeout 120 \
        --access-logfile - \
        --error-logfile - \
        wsgi:app
fi
