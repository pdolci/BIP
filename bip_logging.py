"""
bip_logging — sistema di logging centralizzato di BooksInPieces.

Caratteristiche:
- Scrive su journald tramite protocollo nativo (SOCK_DGRAM su
  /run/systemd/journal/socket) che imposta SYSLOG_IDENTIFIER=bip e
  associa i messaggi all'unità systemd tramite cgroup lookup.
- Fallback a SysLogHandler (dev-log / /dev/log / UDP 514) se il socket
  nativo non è disponibile.
- Scrive sempre anche su sys.__stderr__ così systemd li indicizza
  sotto l'unità del servizio (journalctl -u booksinpieces.service).
- Livelli supportati: info, warning, error, critical.
- Il livello attivo è persistito in AppSetting (DB) e leggibile/modificabile
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
import socket
import struct
import sys
import threading
import time

# ─── Costanti ────────────────────────────────────────────────────────────────

APP_NAME = "bip"

# Socket nativo journald (protocollo strutturato).
# Invia campi KEY=VALUE come SOCK_DGRAM; journald associa il messaggio
# all'unità systemd tramite SCM_CREDENTIALS + cgroup lookup del PID mittente.
# Diversamente dal socket compat syslog (dev-log), questo protocollo imposta
# SYSLOG_IDENTIFIER in modo affidabile anche da processi worker di gunicorn.
_JOURNALD_NATIVE_SOCKET = "/run/systemd/journal/socket"

# Fallback legacy: socket syslog-compat di journald, poi /dev/log, poi UDP.
_JOURNALD_SOCKET = "/run/systemd/journal/dev-log"
_SYSLOG_SOCKET   = "/dev/log"

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


# ─── Handler nativo journald ──────────────────────────────────────────────────

class _JournaldNativeHandler(logging.Handler):
    """
    Invia i record a journald tramite il socket nativo strutturato
    (/run/systemd/journal/socket).

    Vantaggi rispetto a SysLogHandler su dev-log:
    - Imposta SYSLOG_IDENTIFIER=bip esplicitamente nel payload strutturato.
    - journald associa il messaggio all'unità systemd tramite SCM_CREDENTIALS
      automaticamente apposti dal kernel su ogni datagramma AF_UNIX.
    - Non dipende dal parsing del formato syslog testuale.
    """

    _SOCKET_PATH = _JOURNALD_NATIVE_SOCKET

    # Mappa livelli Python → priorità syslog (RFC 5424).
    _PRIORITY: dict[int, int] = {
        logging.CRITICAL: 2,   # LOG_CRIT
        logging.ERROR:    3,   # LOG_ERR
        logging.WARNING:  4,   # LOG_WARNING
        logging.INFO:     6,   # LOG_INFO
        logging.DEBUG:    7,   # LOG_DEBUG
    }

    def __init__(self) -> None:
        super().__init__()
        try:
            self._sock: socket.socket | None = socket.socket(
                socket.AF_UNIX, socket.SOCK_DGRAM
            )
        except OSError:
            self._sock = None

    @classmethod
    def _syslog_priority(cls, levelno: int) -> int:
        for threshold, priority in sorted(cls._PRIORITY.items(), reverse=True):
            if levelno >= threshold:
                return priority
        return 7  # LOG_DEBUG

    @staticmethod
    def _encode_field(key: str, value: str) -> bytes:
        """Codifica un campo nel formato nativo journald."""
        encoded_value = value.encode("utf-8", errors="replace")
        if b"\n" in encoded_value:
            # Formato binario per valori con newline embedded.
            return (
                key.encode() + b"\n"
                + struct.pack("<Q", len(encoded_value))
                + encoded_value + b"\n"
            )
        return f"{key}={value}\n".encode("utf-8", errors="replace")

    def emit(self, record: logging.LogRecord) -> None:
        if self._sock is None:
            return
        try:
            msg = self.format(record)
            data = (
                self._encode_field("MESSAGE", msg)
                + self._encode_field("PRIORITY", str(self._syslog_priority(record.levelno)))
                + self._encode_field("SYSLOG_IDENTIFIER", APP_NAME)
                + self._encode_field("SYSLOG_PID", str(record.process))
            )
            self._sock.sendto(data, self._SOCKET_PATH)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        super().close()


# ─── Setup ───────────────────────────────────────────────────────────────────

def setup_logging(app) -> logging.Logger:
    """
    Configura il logger radice «bip» con due handler:

    1. Handler journald (nativo o syslog-compat): per journalctl -t bip.
    2. Handler stderr (su sys.__stderr__): systemd cattura l'FD originale
       e lo indicizza sotto l'unità del servizio (journalctl -u <servizio>).

    Chiamare una volta sola dentro create_app(), dopo db.init_app(app).

    Returns:
        Il logger «bip» già configurato.
    """
    logger = logging.getLogger(APP_NAME)
    logger.handlers.clear()
    logger.propagate = False
    # Il logger accetta tutto; è il Filter a decidere cosa filtrare.
    logger.setLevel(logging.DEBUG)

    dyn_filter = _DynamicLevelFilter()

    # ── Handler journald ─────────────────────────────────────────────────────
    # Preferisce il protocollo nativo (SYSLOG_IDENTIFIER garantito).
    # Fallback a SysLogHandler se il socket nativo non è presente.
    if os.path.exists(_JOURNALD_NATIVE_SOCKET):
        _handler_desc = f"journald-native:{_JOURNALD_NATIVE_SOCKET}"
        journald_handler: logging.Handler = _JournaldNativeHandler()
        # Il formato va nel campo MESSAGE; SYSLOG_IDENTIFIER è impostato
        # separatamente dal handler, quindi non serve il prefisso "bip[pid]:".
        journald_fmt = logging.Formatter("%(levelname)s %(name)s %(message)s")
    elif os.path.exists(_JOURNALD_SOCKET):
        _handler_desc = f"syslog-compat:{_JOURNALD_SOCKET}"
        journald_handler = logging.handlers.SysLogHandler(
            address=_JOURNALD_SOCKET,
            facility=logging.handlers.SysLogHandler.LOG_LOCAL0,
            socktype=socket.SOCK_DGRAM,
        )
        journald_handler.append_nul = False  # type: ignore[attr-defined]
        journald_fmt = logging.Formatter(
            f"{APP_NAME}[%(process)d]: %(levelname)s %(name)s %(message)s"
        )
    elif os.path.exists(_SYSLOG_SOCKET):
        _handler_desc = f"syslog:{_SYSLOG_SOCKET}"
        journald_handler = logging.handlers.SysLogHandler(
            address=_SYSLOG_SOCKET,
            facility=logging.handlers.SysLogHandler.LOG_LOCAL0,
        )
        journald_fmt = logging.Formatter(
            f"{APP_NAME}[%(process)d]: %(levelname)s %(name)s %(message)s"
        )
    else:
        # Fallback: UDP 514 (macOS, container senza journald, ecc.)
        _handler_desc = "syslog-udp:localhost:514"
        journald_handler = logging.handlers.SysLogHandler(address=("localhost", 514))
        journald_fmt = logging.Formatter(
            f"{APP_NAME}[%(process)d]: %(levelname)s %(name)s %(message)s"
        )

    journald_handler.setFormatter(journald_fmt)
    journald_handler.addFilter(dyn_filter)
    logger.addHandler(journald_handler)

    # ── Handler stderr ───────────────────────────────────────────────────────
    # Usa sys.__stderr__ (FD 2 originale) invece di sys.stderr per evitare
    # che gunicorn o altri framework abbiano sostituito il proxy sys.stderr
    # con un proprio stream non catturato da systemd.
    # systemd cattura FD 2 del processo e indicizza i messaggi sotto
    # _SYSTEMD_UNIT=<nome-servizio>, rendendoli visibili con:
    #   journalctl -u booksinpieces.service
    stderr_handler = logging.StreamHandler(sys.__stderr__)
    if app.debug:
        stderr_fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    else:
        stderr_fmt = logging.Formatter("%(levelname)s %(name)s %(message)s")
    stderr_handler.setFormatter(stderr_fmt)
    stderr_handler.addFilter(dyn_filter)
    logger.addHandler(stderr_handler)

    # Primo messaggio di avvio: conferma quale handler è in uso.
    # Visibile sia in journalctl -t bip che journalctl -u <servizio>.
    logger.info("Logging avviato — handler=%s pid=%d", _handler_desc, os.getpid())

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
