# BooksInPieces

Piattaforma web per la lettura graduale di libri in formato digitale. Gli utenti si iscrivono a uno o più libri e ricevono estratti periodici via email o Telegram, secondo un programma di lettura personalizzato.

---

## Indice

- [Funzionalità](#funzionalità)
- [Stack tecnologico](#stack-tecnologico)
- [Architettura](#architettura)
- [Prerequisiti](#prerequisiti)
- [Configurazione](#configurazione)
- [Avvio in sviluppo](#avvio-in-sviluppo)
- [Avvio in produzione](#avvio-in-produzione)
- [Gestione database](#gestione-database)
- [Sicurezza](#sicurezza)
- [Ruoli utente](#ruoli-utente)
- [Formati libro supportati](#formati-libro-supportati)

---

## Funzionalità

**Per gli utenti:**
- Registrazione con conferma email obbligatoria
- Scoperta libri con ricerca testuale e semantica (FAISS)
- Fino a 3 sottoscrizioni attive contemporaneamente
- Configurazione per sottoscrizione: frequenza, orario, velocità di lettura, canale di consegna
- Modalità pausa, snooze (24h o prossima consegna), modalità viaggio
- Consegna immediata tramite link firmato nell'email
- Dashboard con progressione lettura e storico consegne
- Canale di consegna: email o Telegram (con fallback automatico su email)

**Per amministratori e content manager:**
- Upload e gestione libri (`.txt`, `.html`, `.htm`)
- Attivazione/disattivazione libri
- Pannello amministrazione: gestione utenti e sottoscrizioni, reset password, log level dinamico

---

## Stack tecnologico

| Componente | Tecnologia |
|------------|-----------|
| Backend | Python 3.11+, Flask 3.x |
| ORM | SQLAlchemy 2.x + Flask-SQLAlchemy |
| Database | MySQL (consigliato), PostgreSQL o SQLite |
| Migrazioni | Alembic + Flask-Migrate |
| Autenticazione | Sessioni Flask + itsdangerous (token firmati) |
| CSRF | Flask-WTF |
| Rate limiting | Flask-Limiter + Redis |
| Email | Flask-Mail (SMTP/Gmail) |
| Telegram | API HTTP nativa (urllib) |
| Ricerca semantica | FAISS (CPU) + NumPy |
| Scheduler | APScheduler 3.x (processo separato) |
| Server WSGI | Gunicorn |

---

## Architettura

L'applicazione è composta da **due processi distinti** che devono essere avviati separatamente:

```
┌─────────────────────────┐     ┌──────────────────────────┐
│   Web app (Gunicorn)    │     │   Scheduler worker       │
│   wsgi.py               │     │   scheduler_worker.py    │
│                         │     │                          │
│  - Gestione utenti      │     │  - Verifica ogni minuto  │
│  - Upload libri         │     │    le sottoscrizioni      │
│  - API routes           │     │    in scadenza           │
│  - Sessioni/auth        │     │  - Invia estratti        │
└──────────┬──────────────┘     └──────────┬───────────────┘
           │                               │
           └───────────────┬───────────────┘
                           │
                    ┌──────▼──────┐
                    │  Database   │
                    │  + Redis    │
                    └─────────────┘
```

---

## Prerequisiti

- Python 3.11+
- Redis (per rate limiting distribuito tra worker Gunicorn)
- Database MySQL/PostgreSQL/SQLite
- Account Gmail con App Password (o altro SMTP)

```bash
pip install -r requirements.txt
```

---

## Configurazione

Copia `.env.example` in `.env` e compila i valori:

```bash
cp .env.example .env
```

### Variabili obbligatorie

| Variabile | Descrizione |
|-----------|-------------|
| `SECRET_KEY` | Chiave crittografica Flask (minimo 32 caratteri casuali) |
| `DATABASE_URL` | URI SQLAlchemy es. `mysql+pymysql://user:pass@host/db?charset=utf8mb4` |
| `MAIL_USERNAME` | Email mittente (es. account Gmail) |
| `MAIL_PASSWORD` | App Password Gmail (non la password dell'account) |

### Variabili opzionali principali

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `MAIL_SERVER` | `smtp.gmail.com` | Server SMTP |
| `MAIL_PORT` | `587` | Porta SMTP |
| `MAIL_USE_TLS` | `true` | Abilita STARTTLS |
| `MAIL_SENDER_NAME` | `BookInPieces` | Nome visualizzato nelle email |
| `APP_TIMEZONE` | `Europe/Rome` | Fuso orario per orari di consegna |
| `TELEGRAM_BOT_TOKEN` | — | Token bot Telegram (opzionale) |
| `APP_BASE_URL` | — | URL pubblico es. `https://miosito.com` — abilita il link "ricevi subito" nelle email |
| `FLASK_DEBUG` | `false` | **Mai `true` in produzione** |

### Sessioni

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `SESSION_INACTIVITY_MINUTES` | `10` | Timeout sessione per inattività |
| `SESSION_COOKIE_SECURE` | `true` | Cookie solo su HTTPS |
| `SESSION_COOKIE_HTTPONLY` | `true` | Cookie non accessibile da JavaScript |
| `SESSION_COOKIE_SAMESITE` | `Lax` | Protezione CSRF sui cookie |
| `PREFERRED_URL_SCHEME` | `https` | Schema URL per link generati |

### Rate limiting

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `LOGIN_RATE_LIMIT_ATTEMPTS` | `5` | Tentativi di login per finestra |
| `LOGIN_RATE_LIMIT_WINDOW_SECONDS` | `300` | Finestra rate limit login (secondi) |
| `FORGOT_PASSWORD_RATE_LIMIT_ATTEMPTS` | `3` | Tentativi reset password per finestra |
| `FORGOT_PASSWORD_RATE_LIMIT_WINDOW_SECONDS` | `600` | Finestra rate limit reset password |
| `REGISTER_RATE_LIMIT_ATTEMPTS` | `10` | Tentativi di registrazione per finestra |
| `REGISTER_RATE_LIMIT_WINDOW_SECONDS` | `3600` | Finestra rate limit registrazione |
| `RATE_LIMIT_STORAGE_URI` | `redis://localhost:6379/0` | URI Redis per rate limiting condiviso |
| `RATELIMIT_FAIL_ON_BACKEND_ERROR` | `true` | Se `true`, l'app non parte senza Redis |

### Upload e proxy

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `MAX_UPLOAD_SIZE_MB` | `10` | Limite dimensione file caricati (MB) |
| `USE_PROXY_FIX` | `true` | Abilita ProxyFix per reverse proxy |
| `PROXY_FIX_X_FOR` | `1` | Numero di hop proxy fidati per X-Forwarded-For |
| `PROXY_FIX_X_PROTO` | `1` | Numero di hop proxy fidati per X-Forwarded-Proto |

---

## Avvio in sviluppo

**1. Setup iniziale** (una sola volta, o dopo ogni modifica al database):

```bash
python server_setup.py
```

Crea le cartelle `uploads/books` e `uploads/covers`, applica le migrazioni Alembic e inizializza le tabelle.

**2. Web app:**

```bash
python app.py
# oppure
flask --app app:app run
```

**3. Scheduler** (in un terminale separato):

```bash
python scheduler_worker.py
```

> Lo scheduler è un processo separato dalla web app: senza di esso le consegne pianificate non vengono eseguite.

---

## Avvio in produzione

**1. Setup** (eseguire ad ogni deploy):

```bash
python server_setup.py
```

**2. Web app con Gunicorn:**

```bash
gunicorn --bind 0.0.0.0:8000 --workers 4 wsgi:app
```

**3. Scheduler worker:**

```bash
python scheduler_worker.py
```

### Configurazione systemd consigliata

Entrambi i processi dovrebbero essere supervisionati da systemd (o supervisor) con `Restart=always`.

Esempio unit file per la web app (`/etc/systemd/system/bip-web.service`):

```ini
[Unit]
Description=BooksInPieces Web App
After=network.target redis.service mysql.service

[Service]
User=www-data
WorkingDirectory=/opt/bip
EnvironmentFile=/opt/bip/.env
ExecStart=/opt/bip/venv/bin/gunicorn --bind 127.0.0.1:8000 --workers 4 wsgi:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Esempio unit file per lo scheduler (`/etc/systemd/system/bip-scheduler.service`):

```ini
[Unit]
Description=BooksInPieces Scheduler
After=network.target redis.service mysql.service

[Service]
User=www-data
WorkingDirectory=/opt/bip
EnvironmentFile=/opt/bip/.env
ExecStart=/opt/bip/venv/bin/python scheduler_worker.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Nginx (reverse proxy consigliato)

```nginx
server {
    listen 443 ssl;
    server_name miosito.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 10M;
    }
}

server {
    listen 80;
    server_name miosito.com;
    return 301 https://$host$request_uri;
}
```

> Con questa configurazione impostare `USE_PROXY_FIX=true` e `PROXY_FIX_X_FOR=1` (default).

---

## Gestione database

Le migrazioni sono gestite con Alembic tramite Flask-Migrate.

```bash
# Applicare le migrazioni pendenti
flask --app app:app db upgrade

# Creare una nuova migrazione dopo aver modificato i modelli
flask --app app:app db migrate -m "descrizione modifica"

# Verificare lo stato delle migrazioni
flask --app app:app db current
```

`python server_setup.py` esegue automaticamente `db upgrade` + `db.create_all()` e va preferito per i deploy.

---

## Sicurezza

### Misure implementate

| Area | Implementazione |
|------|----------------|
| Password | Bcrypt via `werkzeug.security`; requisiti: 8–15 caratteri, almeno una lettera, un numero e un simbolo (`!£$%&^`) |
| Timing attack | Hash fittizio eseguito anche per utenti inesistenti durante il login |
| Sessioni | HttpOnly, Secure, SameSite=Lax; session version per revoca remota; timeout inattività |
| CSRF | Flask-WTF su tutti i form POST |
| SQL injection | Impossibile: ORM SQLAlchemy con query parametrizzate |
| XSS | Jinja2 auto-escaping; sanitizzazione HTML upload con whitelist di tag |
| Rate limiting | Login, reset password e registrazione limitati per IP; Redis per condivisione tra worker |
| Token | itsdangerous con scadenza e salt separati per email confirmation (24h) e password reset (1h) |
| File upload | Whitelist estensioni; filename UUID per evitare collisioni; limite dimensione 10 MB |
| Header HTTP | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy` |
| Proxy | ProxyFix configurabile; `X-Forwarded-For` trusted solo per N hop configurati |

### Note operative

- **HTTPS è obbligatorio in produzione.** Impostare `SESSION_COOKIE_SECURE=true` (default) e terminare TLS a livello Nginx/Caddy con redirect 301 da HTTP.
- **Redis deve essere sempre disponibile** se `RATELIMIT_FAIL_ON_BACKEND_ERROR=true` (default). Garantire alta disponibilità di Redis o abbassare il flag e accettare il fallback in memoria (meno sicuro con più worker).
- **Ruotare `SECRET_KEY`** invalida tutte le sessioni attive e i token in volo (email confirmation, password reset, deliver_now). Pianificare di conseguenza.
- I file dei libri e delle copertine sono salvati fuori dalla webroot (`uploads/`) e non sono accessibili direttamente senza autenticazione (tranne le copertine, pubbliche).

### Checklist pre-deploy

- [ ] `SECRET_KEY` generata con `python -c "import secrets; print(secrets.token_hex(32))"`
- [ ] `FLASK_DEBUG=false`
- [ ] `SESSION_COOKIE_SECURE=true`
- [ ] HTTPS attivo con redirect da HTTP
- [ ] Redis in esecuzione e raggiungibile
- [ ] Backup automatico del database configurato
- [ ] Entrambi i processi (web + scheduler) sotto supervisione systemd

---

## Ruoli utente

| Ruolo | Accesso |
|-------|---------|
| Utente non autenticato | Homepage, login, registrazione, reset password |
| Utente autenticato | Scoperta libri, gestione sottoscrizioni, profilo |
| Content Manager (`is_content_manager`) | + Upload, modifica, attivazione/disattivazione libri; test invio email |
| Admin (`is_admin`) | + Tutto il pannello di manutenzione: gestione utenti, reset password, log level dinamico |

Il primo utente admin va creato direttamente nel database o via migrazione dedicata. Non esiste una route pubblica per la creazione di admin.

---

## Formati libro supportati

| Formato | Note |
|---------|------|
| `.txt` | Testo semplice; encoding rilevato automaticamente con chardet |
| `.html` / `.htm` | Sanitizzato al momento del caricamento (whitelist tag); struttura narrativa preservata per il chunking |

I file vengono salvati con nome UUID per evitare collisioni. La dimensione massima è configurabile via `MAX_UPLOAD_SIZE_MB` (default 10 MB).

Il conteggio parole viene calcolato al caricamento e usato per stimare la progressione di lettura e il numero di sessioni rimanenti.
