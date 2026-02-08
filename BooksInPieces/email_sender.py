import logging
import datetime
import html
import re
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


def html_to_text(content):
    """Converte un contenuto HTML in testo leggibile."""
    content = re.sub(r"<script.*?>.*?</script>", "", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"<style.*?>.*?</style>", "", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"</?(p|div|h[1-6]|li|ul|ol|blockquote|section|article|br)\b[^>]*>", "\n", content, flags=re.IGNORECASE)
    content = re.sub(r"<[^>]+>", "", content)
    content = html.unescape(content)
    return re.sub(r"\n\s*\n+", "\n\n", content).strip()


def build_email_bodies(chunk_text, is_html_source=False):
    """Genera corpo testo e corpo HTML per l'email."""
    plain_text = html_to_text(chunk_text) if is_html_source else chunk_text.strip()

    if is_html_source:
        cleaned_html = re.sub(r"<script.*?>.*?</script>", "", chunk_text, flags=re.IGNORECASE | re.DOTALL)
        html_body = f"<div style='font-family: Arial, sans-serif; line-height: 1.6;'>{cleaned_html}</div>"
    else:
        escaped_text = html.escape(plain_text).replace("\n", "<br>")
        html_body = (
            "<div style='font-family: Arial, sans-serif; line-height: 1.6;'>"
            f"{escaped_text}"
            "</div>"
        )

    return plain_text, html_body

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
            readable_content = html_to_text(source_content) if source_is_html else source_content
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
            
            return chunk_text, new_index, source_is_html
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
