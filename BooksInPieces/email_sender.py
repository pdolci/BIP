import logging
import datetime
import html
import re
from dataclasses import dataclass
from flask_mail import Message
from extensions import mail, db
from models import ReadingSchedule, Book, User
from config import Config
import chardet

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
    content = re.sub(r"<script.*?>.*?</script>", "", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"<style.*?>.*?</style>", "", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"</?(p|div|h[1-6]|li|ul|ol|blockquote|section|article|br)\b[^>]*>", "\n", content, flags=re.IGNORECASE)
    content = re.sub(r"<[^>]+>", "", content)
    content = html.unescape(content)
    return re.sub(r"\n\s*\n+", "\n\n", content).strip()


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
    chunk_plain_words = []
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

        words = token.split()
        if not words:
            if started and not finished:
                output_tokens.append(token)
            continue

        token_start = current_word_index
        token_end = current_word_index + len(words)
        current_word_index = token_end

        if token_end <= start_idx:
            continue

        relative_start = max(0, start_idx - token_start)
        selected_words = words[relative_start:]
        if not selected_words:
            continue

        if not started:
            started = True
            for tag in active_tags:
                output_tokens.append(f"<{tag}>")

        kept_words = []
        for word in selected_words:
            kept_words.append(word)
            chunk_plain_words.append(word)
            chunk_word_count += 1

            if chunk_word_count >= length and sentence_end.search(word):
                finished = True
                break

        output_tokens.append(" ".join(kept_words))

        if finished:
            break

    if not chunk_plain_words:
        return None, start_idx

    new_index = start_idx + chunk_word_count
    chunk_html = "".join(output_tokens)

    if started:
        for tag in reversed(active_tags):
            chunk_html += f"</{tag}>"

    return ContentChunk(plain_text=" ".join(chunk_plain_words), html_content=chunk_html), new_index


def detect_encoding(file_path):
    """ Rileva la codifica del file per evitare errori di lettura. """
    with open(file_path, "rb") as f:
        raw_data = f.read(10000)  # Legge un pezzo di file per determinare l'encoding
        result = chardet.detect(raw_data)
        return result["encoding"]


def read_file_chunk(file_path, start_idx, length):
    """Reads a chunk of the book file starting from `start_idx`, ensuring correct word count while preserving original formatting and ending at a full sentence."""
    try:
        encoding = detect_encoding(file_path)  # ✅ Detect file encoding
        with open(file_path, 'r', encoding=encoding) as file:
            logging.info(f"📖 File aperto con successo: {file_path}, partendo da {start_idx}")

            source_content = file.read()
            source_is_html = is_html_file(file_path)
            if source_is_html:
                chunk, new_index = extract_html_chunk(source_content, start_idx, length)
                if chunk is None:
                    logging.info("⚠️ Nessun altro testo da inviare, fine della lettura.")
                    return None, start_idx, source_is_html

                logging.info(f"📖 Chunk HTML letto ({len(chunk.plain_text.split())} parole): {chunk.plain_text[:100]}...")
                return chunk, new_index, source_is_html

            readable_content = source_content
            words = readable_content.split()
            
            if start_idx >= len(words):  # Check if start index exceeds word count
                logging.info("⚠️ Nessun altro testo da inviare, fine della lettura.")
                return None, start_idx, source_is_html

            # Extract the required number of words
            chunk_words = words[start_idx:start_idx + length]
            new_index = start_idx + length

            # Ensure the chunk ends at the end of a sentence
            sentence_endings = {'.', '!', '?'}
            while new_index < len(words) and words[new_index - 1][-1] not in sentence_endings:
                chunk_words.append(words[new_index])
                new_index += 1

            chunk_text = " ".join(chunk_words)
            
            logging.info(f"📖 Chunk letto ({len(chunk_words)} parole): {chunk_text[:100]}...")  # Anteprima primo pezzo
            
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

    text_body, html_body = build_email_bodies(chunk, is_html_source=source_is_html)
    send_email(user.email, f"{book.title} - Nuova lettura", text_body, html_body=html_body)

    # Instead of sending an email, print the chunk
    #logging.info(f"📖 Chunk for {user.email} - {book.title}:")
    #print("\n" + "="*50 + f"\n📖 {book.title} - Next Reading for {user.email}\n" + "="*50)
    #print(chunk)  # Print the chunk instead of sending the email
    #print("="*50 + "\n")

    try:
        schedule.last_sent_index = new_index
        schedule.next_send_date = datetime.datetime.utcnow() + datetime.timedelta(days=schedule.frequency_days)
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
