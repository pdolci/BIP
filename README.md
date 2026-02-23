# BooksInPieces

## Prerequisiti

- Python 3.11+
- Dipendenze installate da `requirements.txt`

```bash
pip install -r requirements.txt
```

## Variabili d'ambiente richieste

Imposta queste variabili prima di avviare l'app:

- `SECRET_KEY`
- `DATABASE_URL`
- `MAIL_USERNAME`
- `MAIL_PASSWORD`

Variabili opzionali principali:

- `MAIL_SERVER` (default: `smtp.gmail.com`)
- `MAIL_PORT` (default: `587`)
- `MAIL_USE_TLS` (default: `true`)
- `APP_TIMEZONE` (default: `Europe/Rome`)
- `TELEGRAM_BOT_TOKEN`
- `FLASK_DEBUG` (default: `false`)
- `SESSION_COOKIE_SECURE` (default: `true`)
- `SESSION_COOKIE_HTTPONLY` (default: `true`)
- `SESSION_COOKIE_SAMESITE` (default: `Lax`)
- `PREFERRED_URL_SCHEME` (default: `https`)
- `USE_PROXY_FIX` (default: `true`)
- `PROXY_FIX_X_FOR` (default: `1`)
- `PROXY_FIX_X_PROTO` (default: `1`)
- `PROXY_FIX_X_HOST` (default: `0`)
- `PROXY_FIX_X_PORT` (default: `0`)
- `PROXY_FIX_X_PREFIX` (default: `0`)

## Avvio in locale (sviluppo)

Web app (sviluppo):

```bash
python app.py
```

oppure con Flask CLI:

```bash
flask --app app:app run
```

Scheduler worker (processo separato):

```bash
python scheduler_worker.py
```

## Avvio in produzione

Web app WSGI (Gunicorn):

```bash
gunicorn --bind 0.0.0.0:8000 wsgi:app
```

Scheduler worker (avviare separatamente dal web):

```bash
python scheduler_worker.py
```

## Note operative

- Lo scheduler **non** viene più avviato automaticamente dal processo web.
- I file libro caricabili supportano `.txt`, `.html` e `.htm`; il parsing di invio converte l'HTML in testo per conteggio parole e chunking.
