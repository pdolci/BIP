import logging
import html
import re
from dataclasses import dataclass
from flask_mail import Message
from extensions import mail, db
from models import ReadingSchedule, Book, User, DeliveryEvent
from config import Config
from schedule_utils import compute_next_send_datetime
from time_utils import utc_now_naive
import chardet

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")

def send_email(to, subject, body, html_body=None):
    """ Invia una email e logga eventuali errori """
    try:
        msg = Message(subject, sender=Config.MAIL_USERNAME, recipients=[to])  # Updated sender
        msg.body = body
        if html_body:
            msg.html = html_body

        logging.info(f"\U0001F4E7 Tentativo di invio email a {to} con oggetto: {subject}")
        mail.send(msg)
        logging.info(f"✅ Email inviata correttamente a {to}")
    except Exception as e:
        logging.error(f"❌ Errore nell'invio email a {to}: {e}")


def is_html_file(file_path):
    return file_path.lower().endswith((".html", ".htm"))


@dataclass
class ContentChunk:
    plain_text: str
    html_content: str | None = None


def html_to_text(content):
    """Converte un contenuto HTML in testo leggibile."""
    content = sanitize_source_text(content)
    content = re.sub(r"<script.*?>.*?</script>", "", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"<style.*?>.*?</style>", "", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"</?(p|div|h[1-6]|li|ul|ol|blockquote|section|article|br)\b[^>]*>", "\n", content, flags=re.IGNORECASE)
    content = re.sub(r"<[^>]+>", "", content)
    content = html.unescape(content)
    return re.sub(r"\n\s*\n+", "\n\n", content).strip()


def sanitize_source_text(content):
    """Rimuove sequenze ANSI e caratteri di controllo preservando il layout testuale."""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    normalized = ANSI_ESCAPE_RE.sub("", normalized)
    normalized = CONTROL_CHARS_RE.sub("", normalized)
    return normalized


def build_email_bodies(chunk, is_html_source=False):
    """Genera corpo testo e corpo HTML per l'email."""
    plain_text = chunk.plain_text.strip() if isinstance(chunk, ContentChunk) else str(chunk).strip()

    if is_html_source:
        raw_html = chunk.html_content if isinstance(chunk, ContentChunk) else str(chunk)
        cleaned_html = re.sub(r"<script.*?>.*?</script>", "", raw_html, flags=re.IGNORECASE | re.DOTALL)
        html_body = cleaned_html
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
    """Estrae un frammento HTML mantenendo la struttura del markup originale."""
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
    """ Rileva la codifica del file per evitare errori di lettura. """
    with open(file_path, "rb") as f:
        raw_data = f.read(10000)  # Legge un pezzo di file per determinare l'encoding
        result = chardet.detect(raw_data)
        return result["encoding"] or "utf-8"




def count_total_words(file_path):
    """Conta il totale parole del contenuto sorgente per metriche progresso/capitolo."""
    try:
        encoding = detect_encoding(file_path)
        with open(file_path, 'r', encoding=encoding, errors='replace') as file:
            content = sanitize_source_text(file.read())
            if is_html_file(file_path):
                content = html_to_text(content)
            return len(re.findall(r"\S+", content))
    except Exception as error:
        logging.warning(f"⚠️ Impossibile contare parole file {file_path}: {error}")
        return 0


def build_email_subject(book_title, part_number, completion_percent):
    return f"{book_title} • Parte {part_number} – ~{completion_percent}%"


def read_file_chunk(file_path, start_idx, length):
    """Reads a chunk of the book file starting from `start_idx`, ensuring correct word count while preserving original formatting and ending at a full sentence."""
    try:
        encoding = detect_encoding(file_path)  # ✅ Detect file encoding
        with open(file_path, 'r', encoding=encoding, errors='replace') as file:
            logging.info(f"📖 File aperto con successo: {file_path}, partendo da {start_idx}")

            source_content = sanitize_source_text(file.read())
            source_is_html = is_html_file(file_path)
            if source_is_html:
                chunk, new_index = extract_html_chunk(source_content, start_idx, length)
                if chunk is None:
                    logging.info("⚠️ Nessun altro testo da inviare, fine della lettura.")
                    return None, start_idx, source_is_html

                logging.info(f"📖 Chunk HTML letto ({len(chunk.plain_text.split())} parole): {chunk.plain_text[:100]}...")
                return chunk, new_index, source_is_html

            readable_content = source_content
            words = list(re.finditer(r"\S+", readable_content))
            
            if start_idx >= len(words):  # Check if start index exceeds word count
                logging.info("⚠️ Nessun altro testo da inviare, fine della lettura.")
                return None, start_idx, source_is_html

            # Extract the required number of words
            end_word_index = min(start_idx + length, len(words))

            # Ensure the chunk ends at the end of a sentence
            sentence_end = re.compile(r"[.!?][\"')\]]?$")
            while end_word_index < len(words) and not sentence_end.search(words[end_word_index - 1].group(0)):
                end_word_index += 1

            chunk_start_char = words[start_idx].start()
            chunk_end_char = words[end_word_index - 1].end()
            chunk_text = readable_content[chunk_start_char:chunk_end_char].strip()
            new_index = end_word_index
            
            logging.info(f"📖 Chunk letto ({new_index - start_idx} parole): {chunk_text[:100]}...")  # Anteprima primo pezzo
            
            return ContentChunk(plain_text=chunk_text), new_index, source_is_html
    except Exception as e:
        logging.error(f"❌ Errore nella lettura del file {file_path}: {e}")
        return None, start_idx, False

def send_next_book_part(schedule_id):
    """ Invia la prossima sezione del libro via email """
    schedule = ReadingSchedule.query.get(schedule_id)

    if not schedule:
        logging.error(f"⚠️ Nessun programma di lettura trovato con ID {schedule_id}")
        return

    book = Book.query.get(schedule.book_id)
    user = User.query.get(schedule.user_id)

    if not book or not user:
        logging.error("⚠️ Errore: Nessun libro o utente trovato")
        return

    file_path = book.get_absolute_path()  # Use method to get path
    chunk, new_index, source_is_html = read_file_chunk(
        file_path,
        schedule.last_sent_index,
        schedule.words_per_minute * schedule.minutes_per_reading,
    )

    if not chunk:
        logging.info("⚠️ Nessun altro testo da inviare, fine della lettura.")
        return

    words_sent = max(new_index - schedule.last_sent_index, 0)
    total_words = count_total_words(file_path)
    part_number = max(1, (schedule.last_sent_index // max(schedule.words_per_minute * schedule.minutes_per_reading, 1)) + 1)
    completion_percent = int(round((new_index / total_words) * 100)) if total_words else 0

    text_body, html_body = build_email_bodies(chunk, is_html_source=source_is_html)
    progress_header = f"Parte {part_number} – ~{completion_percent}%"
    text_body = f"{progress_header}\n\n{text_body}"
    html_body = (
        "<div style='font-family: Arial, sans-serif; color: #334155; margin-bottom: 16px; font-weight: 600;'>"
        f"{html.escape(progress_header)}"
        "</div>" + html_body
    )

    send_email(user.email, build_email_subject(book.title, part_number, completion_percent), text_body, html_body=html_body)

    # Instead of sending an email, print the chunk
    #logging.info(f"📖 Chunk for {user.email} - {book.title}:")
    #print("\n" + "="*50 + f"\n📖 {book.title} - Next Reading for {user.email}\n" + "="*50)
    #print(chunk)  # Print the chunk instead of sending the email
    #print("="*50 + "\n")

    try:
        schedule.last_sent_index = new_index
        schedule.next_send_date = compute_next_send_datetime(utc_now_naive(), schedule, allow_immediate=False)
        db.session.commit()  # Removed unnecessary add()
        logging.info(f"✅ Database aggiornato per l'utente {user.email}")
    except Exception as e:
        db.session.rollback()
        logging.error(f"❌ Errore nel commit del database: {e}")




def send_password_reset_email(user_email, reset_url):
    """Invia l'email con link per il recupero password."""
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
