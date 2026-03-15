import calendar
import hashlib
import hmac
import html
import json
import re
import time as _time
import urllib.parse
import urllib.error
import urllib.request
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass

import chardet
from bip_logging import get_logger
from html_processing import sanitize_uploaded_html
from flask_mail import Message
from sqlalchemy.orm import joinedload

from config import Config
from extensions import db, mail
from models import DeliveryEvent, ReadingSchedule
from time_utils import utc_now_naive, compute_next_send_utc

logger = get_logger(__name__)

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")

DELIVERY_EMAIL = "email"
DELIVERY_TELEGRAM = "telegram"
SUPPORTED_DELIVERY_CHANNELS = {DELIVERY_EMAIL, DELIVERY_TELEGRAM}
TELEGRAM_MAX_MESSAGE_LENGTH = 4000

def _deliver_now_sign(payload: str) -> str:
    return hmac.new(Config.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def generate_deliver_now_token(schedule_id: int, user_id: int, expiry: int) -> str:
    """Genera un token firmato valido fino a ``expiry`` (Unix timestamp UTC)."""
    payload = f"{schedule_id}:{user_id}:{expiry}"
    sig = _deliver_now_sign(payload)
    return urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()


def verify_deliver_now_token(token: str) -> tuple[int | None, int | None]:
    try:
        padded = token + "=" * (-len(token) % 4)
        decoded = urlsafe_b64decode(padded.encode()).decode()
        schedule_id_str, user_id_str, expiry_str, sig = decoded.split(":", 3)
        payload = f"{schedule_id_str}:{user_id_str}:{expiry_str}"
        if not hmac.compare_digest(_deliver_now_sign(payload), sig):
            return None, None
        if _time.time() > int(expiry_str):
            return None, None
        return int(schedule_id_str), int(user_id_str)
    except Exception:
        return None, None


@dataclass
class ContentChunk:
    plain_text: str
    html_content: str | None = None


def send_email(to, subject, body, html_body=None):
    try:
        msg = Message(subject, sender=Config.MAIL_USERNAME, recipients=[to])
        msg.body = body
        if html_body:
            msg.html = html_body
        mail.send(msg)
        logger.info(f"✅ Email inviata correttamente a {to}")
        return True
    except Exception as error:
        logger.error(f"❌ Errore nell'invio email a {to}: {error}")
        return False


def send_email_confirmation_request(to, confirmation_url):
    subject = "Conferma il tuo indirizzo email"
    body = (
        "Ciao!\n\n"
        "Grazie per esserti registrato. Per attivare l'account, conferma il tuo indirizzo email visitando questo link:\n"
        f"{confirmation_url}\n\n"
        "Se non hai richiesto tu la registrazione, puoi ignorare questa email."
    )
    html_body = (
        "<div style='font-family: Arial, sans-serif; line-height: 1.6;'>"
        "<p>Ciao!</p>"
        "<p>Grazie per esserti registrato. Per attivare l'account, conferma il tuo indirizzo email cliccando qui:</p>"
        f"<p><a href='{html.escape(confirmation_url, quote=True)}'>Conferma indirizzo email</a></p>"
        "<p>Se non hai richiesto tu la registrazione, puoi ignorare questa email.</p>"
        "</div>"
    )
    return send_email(to, subject, body, html_body)


def send_telegram_message(chat_id, message):
    token = Config.TELEGRAM_BOT_TOKEN
    if not token:
        logger.error("❌ TELEGRAM_BOT_TOKEN non configurato: impossibile inviare su Telegram.")
        return False

    chat_id = (chat_id or "").strip()
    if not chat_id:
        logger.error("❌ chat_id Telegram non valido.")
        return False

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"

    def split_message(message_text, max_length):
        normalized = (message_text or "").strip()
        if not normalized:
            return []

        chunks = []
        text_to_process = normalized
        while text_to_process:
            if len(text_to_process) <= max_length:
                chunks.append(text_to_process)
                break

            split_index = text_to_process.rfind("\n", 0, max_length + 1)
            if split_index <= 0:
                split_index = text_to_process.rfind(" ", 0, max_length + 1)
            if split_index <= 0:
                split_index = max_length

            chunk = text_to_process[:split_index].strip()
            if not chunk:
                chunk = text_to_process[:max_length]
                split_index = max_length

            chunks.append(chunk)
            text_to_process = text_to_process[split_index:].lstrip()

        return chunks

    messages = split_message(message, TELEGRAM_MAX_MESSAGE_LENGTH)
    if not messages:
        logger.error("❌ Messaggio Telegram vuoto: invio annullato.")
        return False

    try:
        for index, message_chunk in enumerate(messages, start=1):
            payload = urllib.parse.urlencode({"chat_id": chat_id, "text": message_chunk}).encode("utf-8")
            req = urllib.request.Request(api_url, data=payload, method="POST")
            with urllib.request.urlopen(req, timeout=10) as response:
                body = json.loads(response.read().decode("utf-8"))
            if not body.get("ok"):
                logger.error(f"❌ Telegram API error (chunk {index}/{len(messages)}): {body}")
                return False
        logger.info(f"✅ Messaggio Telegram inviato a chat_id {chat_id} in {len(messages)} parte/i")
        return True
    except urllib.error.HTTPError as error:
        response_body = ""
        try:
            response_body = error.read().decode("utf-8", errors="replace")
        except Exception:
            response_body = "<impossibile leggere il body della risposta Telegram>"
        logger.error(
            "❌ Telegram HTTP error per chat_id %s: status=%s reason=%s response=%s",
            chat_id,
            error.code,
            error.reason,
            response_body,
        )
    except urllib.error.URLError as error:
        logger.error("❌ Telegram URL error per chat_id %s: reason=%s", chat_id, error.reason)
    except Exception as error:
        logger.exception(f"❌ Errore invio Telegram a chat_id {chat_id}: {error}")

    return False


def is_html_file(file_path):
    return file_path.lower().endswith((".html", ".htm"))


def sanitize_source_text(content):
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    normalized = ANSI_ESCAPE_RE.sub("", normalized)
    normalized = CONTROL_CHARS_RE.sub("", normalized)
    return normalized


def normalize_html_for_chunking(content):
    """Sanitizza HTML e preserva la struttura narrativa per chunking stabile."""
    normalized = sanitize_source_text(content)
    cleaned = sanitize_uploaded_html(normalized)

    body_match = re.search(r"<body\b[^>]*>(.*?)</body>", cleaned, flags=re.IGNORECASE | re.DOTALL)
    if body_match:
        return body_match.group(1)

    return cleaned


def html_to_text(content):
    content = sanitize_source_text(content)
    content = re.sub(r"<script.*?>.*?</script>", "", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"<style.*?>.*?</style>", "", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"</?(p|div|h[1-6]|li|ul|ol|blockquote|section|article|br)\b[^>]*>", "\n", content, flags=re.IGNORECASE)
    content = re.sub(r"<[^>]+>", "", content)
    content = html.unescape(content)
    return re.sub(r"\n\s*\n+", "\n\n", content).strip()


def build_email_bodies(chunk, is_html_source=False):
    plain_text = chunk.plain_text.strip() if isinstance(chunk, ContentChunk) else str(chunk).strip()

    if is_html_source:
        raw_html = chunk.html_content if isinstance(chunk, ContentChunk) else str(chunk)
        html_body = sanitize_uploaded_html(raw_html)
    else:
        escaped_text = html.escape(plain_text).replace("\n", "<br>")
        html_body = (
            "<div style='font-family: Arial, sans-serif; line-height: 1.6;'>"
            f"{escaped_text}"
            "</div>"
        )

    return plain_text, html_body


def _tag_name(tag_token):
    match = re.match(r"<\s*/?\s*([a-zA-Z0-9:-]+)", tag_token)
    return match.group(1).lower() if match else None


def _is_closing_tag(tag_token):
    return bool(re.match(r"<\s*/", tag_token))


def _is_self_closing_tag(tag_token):
    tag_name = _tag_name(tag_token)
    if not tag_name:
        return True
    if tag_token.rstrip().endswith("/>"):
        return True
    return tag_name in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def extract_html_chunk(content, start_idx, length):
    tokens = re.findall(r"<[^>]+>|[^<]+", content)
    sentence_end = re.compile(r"[.!?][\"')\]]?$")
    current_word_index = 0
    chunk_word_count = 0
    output_tokens = []
    active_tags = []
    started = False
    finished = False

    for token in tokens:
        if token.startswith("<"):
            tag = _tag_name(token)
            if tag and not _is_self_closing_tag(token):
                if _is_closing_tag(token):
                    if tag in active_tags:
                        active_tags.reverse()
                        active_tags.remove(tag)
                        active_tags.reverse()
                else:
                    active_tags.append(tag)

            if started and not finished:
                output_tokens.append(token)
            continue

        word_matches = list(re.finditer(r"\S+", token))
        if not word_matches:
            if started and not finished:
                output_tokens.append(token)
            continue

        token_start = current_word_index
        token_end = current_word_index + len(word_matches)
        current_word_index = token_end

        if token_end <= start_idx:
            continue

        relative_start = max(0, start_idx - token_start)
        if relative_start >= len(word_matches):
            continue

        if not started:
            started = True
            for tag in active_tags:
                output_tokens.append(f"<{tag}>")

        selected_start_char = word_matches[relative_start].start()
        selected_end_char = len(token)

        for match in word_matches[relative_start:]:
            word = match.group(0)
            chunk_word_count += 1
            selected_end_char = match.end()

            if chunk_word_count >= length and sentence_end.search(word):
                finished = True
                break

        output_tokens.append(token[selected_start_char:selected_end_char])

        if finished:
            break

    if chunk_word_count == 0:
        return None, start_idx

    new_index = start_idx + chunk_word_count
    chunk_html = "".join(output_tokens)

    if started:
        for tag in reversed(active_tags):
            chunk_html += f"</{tag}>"

    return ContentChunk(plain_text=html_to_text(chunk_html), html_content=chunk_html), new_index


def detect_encoding(file_path):
    with open(file_path, "rb") as source:
        raw_data = source.read(10000)
        result = chardet.detect(raw_data)
        return result["encoding"] or "utf-8"


def count_total_words(file_path):
    try:
        encoding = detect_encoding(file_path)
        with open(file_path, "r", encoding=encoding, errors="replace") as source:
            content = sanitize_source_text(source.read())
            if is_html_file(file_path):
                content = html_to_text(normalize_html_for_chunking(content))
            return len(re.findall(r"\S+", content))
    except Exception as error:
        logger.warning(f"⚠️ Impossibile contare parole file {file_path}: {error}")
        return 0


def build_email_subject(book_title, part_number, completion_percent):
    return f"{book_title} • Parte {part_number} – ~{completion_percent}%"


def read_file_chunk(file_path, start_idx, length):
    try:
        encoding = detect_encoding(file_path)
        with open(file_path, "r", encoding=encoding, errors="replace") as source:
            source_content = sanitize_source_text(source.read())
            source_is_html = is_html_file(file_path)
            if source_is_html:
                normalized_html = normalize_html_for_chunking(source_content)
                chunk, new_index = extract_html_chunk(normalized_html, start_idx, length)
                if chunk is None:
                    return None, start_idx, source_is_html
                return chunk, new_index, source_is_html

            words = list(re.finditer(r"\S+", source_content))
            if start_idx >= len(words):
                return None, start_idx, source_is_html

            end_word_index = min(start_idx + length, len(words))
            sentence_end = re.compile(r"[.!?][\"')\]]?$")
            while end_word_index < len(words) and not sentence_end.search(words[end_word_index - 1].group(0)):
                end_word_index += 1

            chunk_start_char = words[start_idx].start()
            chunk_end_char = words[end_word_index - 1].end()
            chunk_text = source_content[chunk_start_char:chunk_end_char].strip()
            return ContentChunk(plain_text=chunk_text), end_word_index, source_is_html
    except Exception as error:
        logger.error(f"❌ Errore nella lettura del file {file_path}: {error}")
        return None, start_idx, False


def _build_delivery_content(chunk, source_is_html, book_title, part_number, completion_percent):
    # Costruisce un payload unico da riutilizzare su canali diversi
    # (Telegram testo puro, Email testo + HTML).
    text_body, html_body = build_email_bodies(chunk, is_html_source=source_is_html)
    progress_header = f"Parte {part_number} – ~{completion_percent}%"
    text_payload = f"{book_title}\n{progress_header}\n\n{text_body}"
    html_payload = (
        "<div style='font-family: Arial, sans-serif; color: #334155; margin-bottom: 16px; font-weight: 600;'>"
        f"{html.escape(progress_header)}"
        "</div>" + html_body
    )
    return text_payload, html_payload


def _resolve_delivery_channel(schedule):
    """Restituisce il canale richiesto, applicando un fallback sicuro a email."""
    if schedule.delivery_channel in SUPPORTED_DELIVERY_CHANNELS:
        return schedule.delivery_channel
    return DELIVERY_EMAIL


def _send_chunk_via_telegram(user, text_payload):
    """
    Invia il contenuto su Telegram.

    Il metodo non gestisce fallback: restituisce solo l'esito tecnico del canale Telegram,
    lasciando al chiamante la decisione su eventuali alternative.
    """
    if not user.telegram_handle:
        logger.warning(f"⚠️ Utente {user.id} senza handle Telegram: impossibile inviare via Telegram.")
        return False

    sent = send_telegram_message(user.telegram_handle, text_payload)
    if not sent:
        logger.warning(f"⚠️ Invio Telegram fallito per user={user.id}.")
    return sent


def _send_chunk_via_email(user, book, text_payload, html_payload, part_number, completion_percent):
    """Invia il contenuto via email mantenendo oggetto e corpo coerenti con la progressione."""
    return send_email(
        user.email,
        build_email_subject(book.title, part_number, completion_percent),
        text_payload,
        html_body=html_payload,
    )


def _deliver_chunk(user, schedule, book, text_payload, html_payload, part_number, completion_percent):
    """
    Orchestra la consegna del chunk separando chiaramente la logica per canale.

    Strategia:
    1) prova il canale configurato dall'utente;
    2) se Telegram fallisce, effettua fallback automatico su email.
    """
    channel = _resolve_delivery_channel(schedule)

    if channel == DELIVERY_TELEGRAM:
        if _send_chunk_via_telegram(user, text_payload):
            return True, DELIVERY_TELEGRAM
        logger.warning(f"⚠️ Attivo fallback email per user={user.id}.")

    email_sent = _send_chunk_via_email(
        user,
        book,
        text_payload,
        html_payload,
        part_number,
        completion_percent,
    )
    return email_sent, DELIVERY_EMAIL


def send_next_book_part(schedule_id):
    # 1) Recupero entità principali della consegna.
    schedule = ReadingSchedule.query.options(
        joinedload(ReadingSchedule.book),
        joinedload(ReadingSchedule.user),
    ).filter_by(id=schedule_id).first()
    if not schedule:
        logger.error(f"⚠️ Nessun programma di lettura trovato con ID {schedule_id}")
        return

    book = schedule.book
    user = schedule.user
    if not book or not user:
        logger.error("⚠️ Errore: Nessun libro o utente trovato")
        return

    file_path = book.get_absolute_path()
    # 2) Estrae il prossimo blocco rispettando il ritmo di lettura configurato.
    chunk, new_index, source_is_html = read_file_chunk(
        file_path,
        schedule.last_sent_index,
        schedule.words_per_minute * schedule.minutes_per_reading,
    )
    if not chunk:
        logger.info("⚠️ Nessun altro testo da inviare, fine della lettura.")
        return

    words_sent = max(new_index - schedule.last_sent_index, 0)
    total_words = book.word_count or count_total_words(file_path)
    if total_words != (book.word_count or 0):
        book.word_count = total_words
    words_per_part = max(schedule.words_per_minute * schedule.minutes_per_reading, 1)
    part_number = max(1, (schedule.last_sent_index // words_per_part) + 1)
    completion_percent = int(round((new_index / total_words) * 100)) if total_words else 0
    text_payload, html_payload = _build_delivery_content(
        chunk,
        source_is_html,
        book.title,
        part_number,
        completion_percent,
    )

    # 3) Se configurato, aggiunge il link "ricevi subito il prossimo estratto".
    base_url = Config.APP_BASE_URL
    logger.info(f"deliver_now footer: APP_BASE_URL={repr(base_url)}")
    if base_url:
        # Il token scade 1 minuto prima del prossimo invio pianificato,
        # così il link non è più cliccabile dopo che la consegna automatica
        # è già avvenuta (evita estratti doppi).
        next_send = compute_next_send_utc(schedule, utc_now_naive(), allow_immediate=False)
        token_expiry = int(calendar.timegm(next_send.timetuple())) - 60
        token = generate_deliver_now_token(schedule.id, user.id, token_expiry)
        deliver_url = f"{base_url}/deliver_now/{token}"
        text_payload += f"\n\n---\nVuoi continuare subito? {deliver_url}"
        html_payload += (
            "<hr style='border:none;border-top:1px solid #e2e8f0;margin:24px 0'>"
            "<p style='font-family:Arial,sans-serif;color:#64748b;font-size:13px;text-align:center;margin:0'>"
            f"Vuoi continuare subito? <a href='{html.escape(deliver_url)}' style='color:#4f46e5;text-decoration:none'>Ricevi subito il prossimo estratto →</a>"
            "</p>"
        )

    # 4) Invio su canale preferito con fallback email se necessario.
    delivered, effective_channel = _deliver_chunk(
        user,
        schedule,
        book,
        text_payload,
        html_payload,
        part_number,
        completion_percent,
    )
    if not delivered:
        return

    try:
        # 5) Persistenza stato avanzamento + audit della consegna.
        schedule.last_sent_index = new_index
        schedule.next_send_date = compute_next_send_utc(schedule, utc_now_naive(), allow_immediate=False)
        db.session.add(
            DeliveryEvent(
                schedule_id=schedule.id,
                event_type="sent",
                words_count=words_sent,
                start_word_index=max(schedule.last_sent_index - words_sent, 0),
                end_word_index=new_index,
                note=f"Consegna via {effective_channel}",
            )
        )
        db.session.commit()
        logger.info(f"✅ Delivery completata per schedule={schedule.id} via {effective_channel}")
    except Exception as error:
        db.session.rollback()
        logger.error(f"❌ Errore nel commit del database: {error}")
        return

    # 6) Se il libro è finito, invia la notifica di completamento.
    if total_words > 0 and new_index >= total_words:
        try:
            send_book_completion_notification(user, book)
            logger.info(f"✅ Notifica completamento inviata per schedule={schedule.id}")
        except Exception as error:
            logger.error(f"❌ Errore nell'invio della notifica di completamento: {error}")


def send_book_completion_notification(user, book):
    """Invia una notifica di completamento lettura all'utente tramite il canale preferito."""
    subject = f"Hai completato '{book.title}' - BooksInPieces"
    plain_body = (
        f"Complimenti!\n\n"
        f"Hai terminato la lettura di '{book.title}'.\n\n"
        f"Torna su BooksInPieces per scegliere il tuo prossimo libro."
    )
    html_body = (
        "<div style='font-family: Arial, sans-serif; line-height: 1.6;'>"
        "<p>Complimenti!</p>"
        f"<p>Hai terminato la lettura di <strong>{html.escape(book.title)}</strong>.</p>"
        "<p>Torna su BooksInPieces per scegliere il tuo prossimo libro.</p>"
        "</div>"
    )
    telegram_handle = user.telegram_handle
    if user.preferred_delivery_channel == DELIVERY_TELEGRAM and telegram_handle:
        sent = send_telegram_message(telegram_handle, f"{subject}\n\n{plain_body}")
        if sent:
            return
        logger.warning("⚠️ Fallback email per notifica completamento user=%s", user.id)
    send_email(user.email, subject, plain_body, html_body=html_body)


def send_password_reset_email(user_email, reset_url):
    subject = "Recupero password - BooksInPieces"
    body = (
        "Hai richiesto il recupero della password.\n\n"
        f"Apri questo link per impostarne una nuova: {reset_url}\n\n"
        "Se non hai richiesto tu questa operazione, ignora questa email."
    )
    html_body = (
        "<div style='font-family: Arial, sans-serif; line-height: 1.6;'>"
        "<p>Hai richiesto il recupero della password.</p>"
        f"<p>Apri questo link per impostarne una nuova: <a href='{html.escape(reset_url)}'>{html.escape(reset_url)}</a></p>"
        "<p>Se non hai richiesto tu questa operazione, ignora questa email.</p>"
        "</div>"
    )
    send_email(user_email, subject, body, html_body=html_body)
