"""
bip_logging — sistema di logging centralizzato di BooksInPieces.

Caratteristiche:
- Scrive su syslog/journald tramite SysLogHandler (gestione affidata all'OS).
- Livelli supportati: info, warning, error, critical.
- Il livello attivo è persisto in AppSetting (DB) e leggibile/modificabile
  a caldo dall'amministratore senza riavviare l'applicazione.
- La lettura del livello è cache-ata in memoria (TTL 30 s) per evitare
  una query al DB ad ogni riga di log.

Utilizzo base:
    from bip_logging import get_logger
    logger = get_logger(__name__)
    logger.info("Messaggio di esempio")
"""

import logging
import logging.handlers
import os
import threading
import time

# ─── Costanti ────────────────────────────────────────────────────────────────

APP_NAME = "bip"

# Socket Unix di syslog/journald su Linux; fallback a UDP 514 altrove.
_SYSLOG_SOCKET = "/dev/log"

# Livelli esposti all'amministratore (in ordine crescente di severità).
LEVELS: dict[str, int] = {
    "info":     logging.INFO,
    "warning":  logging.WARNING,
    "error":    logging.ERROR,
    "critical": logging.CRITICAL,
}
_LEVEL_NAMES: dict[int, str] = {v: k for k, v in LEVELS.items()}

# TTL della cache in-memory del livello attivo (secondi).
_CACHE_TTL = 30.0

# ─── Cache thread-safe del livello attivo ────────────────────────────────────

_lock        = threading.Lock()
_cached_level: int   = logging.INFO
_cache_until: float  = 0.0          # monotonic time dopo cui la cache scade


def _fetch_level_from_db() -> int:
    """Legge il livello corrente da AppSetting; restituisce INFO in caso di errore."""
    try:
        # Import lazy per evitare dipendenze circolari al momento del modulo.
        from models import AppSetting  # noqa: PLC0415
        setting = AppSetting.query.filter_by(key="log_level").first()
        if setting and setting.value in LEVELS:
            return LEVELS[setting.value]
    except Exception:
        pass
    return logging.INFO


def _get_effective_level() -> int:
    """Restituisce il livello attivo, aggiornando la cache se scaduta."""
    global _cached_level, _cache_until
    now = time.monotonic()
    if now >= _cache_until:
        with _lock:
            if now >= _cache_until:          # doppio check dentro il lock
                _cached_level = _fetch_level_from_db()
                _cache_until  = now + _CACHE_TTL
    return _cached_level


# ─── Filter dinamico ─────────────────────────────────────────────────────────

class _DynamicLevelFilter(logging.Filter):
    """Lascia passare solo i record con severità >= livello attivo in DB."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= _get_effective_level()


# ─── Setup ───────────────────────────────────────────────────────────────────

def setup_logging(app) -> logging.Logger:
    """
    Configura il logger radice «bip» e lo attacca al SysLogHandler dell'OS.

    Chiamare una volta sola dentro create_app(), dopo db.init_app(app).
    In modalità debug aggiunge anche uno StreamHandler su stderr.

    Returns:
        Il logger «bip» già configurato.
    """
    logger = logging.getLogger(APP_NAME)
    logger.handlers.clear()
    logger.propagate = False
    # Il logger accetta tutto; è il Filter a decidere cosa filtrare.
    logger.setLevel(logging.DEBUG)

    dyn_filter = _DynamicLevelFilter()

    # Ident fisso "bip" → journald imposta SYSLOG_IDENTIFIER=bip su ogni
    # record, rendendo possibile filtrare con: journalctl -t bip
    # Il nome del logger (modulo) va nel corpo del messaggio.
    fmt = logging.Formatter(f"{APP_NAME}[%(process)d]: %(levelname)s %(name)s %(message)s")

    # Handler syslog/journald — l'OS gestisce rotazione, compressione e indice.
    if os.path.exists(_SYSLOG_SOCKET):
        syslog_handler: logging.Handler = logging.handlers.SysLogHandler(
            address=_SYSLOG_SOCKET,
            facility=logging.handlers.SysLogHandler.LOG_LOCAL0,
        )
    else:
        # Fallback: UDP 514 (macOS, container senza journald, ecc.)
        syslog_handler = logging.handlers.SysLogHandler(address=("localhost", 514))

    syslog_handler.setFormatter(fmt)
    syslog_handler.addFilter(dyn_filter)
    logger.addHandler(syslog_handler)

    if app.debug:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(
            logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
        )
        stream_handler.addFilter(dyn_filter)
        logger.addHandler(stream_handler)

    return logger


# ─── API pubblica ─────────────────────────────────────────────────────────────

def get_logger(name: str | None = None) -> logging.Logger:
    """
    Restituisce un logger figlio del logger «bip».

    Args:
        name: suffisso del logger (tipicamente __name__ del modulo chiamante).

    Examples:
        logger = get_logger(__name__)
        logger.info("Avvio completato")
    """
    if name:
        # Normalizza "bip.bip_logging" → "bip.bip_logging", ma evita "bip.bip.xxx"
        qualified = name if name.startswith(f"{APP_NAME}.") else f"{APP_NAME}.{name}"
        return logging.getLogger(qualified)
    return logging.getLogger(APP_NAME)


def set_log_level(level_name: str) -> None:
    """
    Persiste il nuovo livello di log in DB e azzera immediatamente la cache.

    Tutti i processi rileveranno il cambiamento entro _CACHE_TTL secondi.

    Args:
        level_name: uno tra «info», «warning», «error», «critical».

    Raises:
        ValueError: se level_name non è tra i valori ammessi.
    """
    global _cached_level, _cache_until

    level_name = level_name.lower().strip()
    if level_name not in LEVELS:
        raise ValueError(
            f"Livello non valido: {level_name!r}. "
            f"Valori ammessi: {', '.join(LEVELS)}"
        )

    from models import AppSetting      # noqa: PLC0415
    from extensions import db          # noqa: PLC0415

    setting = AppSetting.query.filter_by(key="log_level").first()
    if setting:
        setting.value = level_name
    else:
        db.session.add(AppSetting(key="log_level", value=level_name))
    db.session.commit()

    with _lock:
        _cached_level = LEVELS[level_name]
        _cache_until  = 0.0   # forza refresh immediato al prossimo log


def get_log_level_name() -> str:
    """Restituisce il nome del livello attivo (es. «warning»)."""
    return _LEVEL_NAMES.get(_get_effective_level(), "info")
