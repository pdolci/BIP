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
- `SESSION_INACTIVITY_MINUTES` (default: `10`)
- `PREFERRED_URL_SCHEME` (default: `https`)
- `USE_PROXY_FIX` (default: `true`)
- `PROXY_FIX_X_FOR` (default: `1`)
- `PROXY_FIX_X_PROTO` (default: `1`)
- `PROXY_FIX_X_HOST` (default: `0`)
- `PROXY_FIX_X_PORT` (default: `0`)
- `PROXY_FIX_X_PREFIX` (default: `0`)
- `STARTUP_GUARDRAILS_ENABLED` (default: `true`; se `true` l'app rifiuta l'avvio se manca il setup cartelle)

## Avvio in locale (sviluppo)

Prima dell'avvio dell'applicazione esegui il setup iniziale in un programma dedicato:

```bash
python server_setup.py
```

Questo comando si occupa di:
- creazione cartelle (`uploads/books`, `uploads/covers`)
- applicazione migrazioni database
- inizializzazione tabelle (se mancanti)

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

Esegui una volta (o ad ogni deploy) il setup:

```bash
python server_setup.py
```

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
- Se il setup non è stato eseguito, web app e worker terminano subito con errore esplicito e istruzione a lanciare `python server_setup.py`.
- I file libro caricabili supportano `.txt`, `.html` e `.htm`; il parsing di invio converte l'HTML in testo per conteggio parole e chunking.
