import logging
import datetime
from flask_mail import Message
from extensions import mail, db
from models import ReadingSchedule, Book, User
from config import Config
import chardet

def send_email(to, subject, body):
    """ Invia una email e logga eventuali errori """
    try:
        msg = Message(subject, sender=Config.MAIL_USERNAME, recipients=[to])  # Updated sender
        msg.body = body

        logging.info(f"\U0001F4E7 Tentativo di invio email a {to} con oggetto: {subject}")
        mail.send(msg)
        logging.info(f"✅ Email inviata correttamente a {to}")
    except Exception as e:
        logging.error(f"❌ Errore nell'invio email a {to}: {e}")

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
            
            lines = file.readlines()  # Read file as lines to preserve formatting
            text = "".join(lines)  # Preserve original formatting
            words = text.split()
            
            if start_idx >= len(words):  # Check if start index exceeds word count
                logging.info("⚠️ Nessun altro testo da inviare, fine della lettura.")
                return None, start_idx

            # Extract the required number of words
            chunk_words = words[start_idx:start_idx + length]
            new_index = start_idx + length

            # Ensure the chunk ends at the end of a sentence
            sentence_endings = {'.', '!', '?'}
            while new_index < len(words) and words[new_index - 1][-1] not in sentence_endings:
                chunk_words.append(words[new_index])
                new_index += 1

            # Find the corresponding lines that contain the extracted words
            chunk_text = ""
            word_count = 0
            for line in lines:
                words_in_line = line.split()
                if word_count >= start_idx and word_count < new_index:
                    chunk_text += line
                word_count += len(words_in_line)
                if word_count >= new_index:
                    break
            
            logging.info(f"📖 Chunk letto ({len(chunk_words)} parole): {chunk_text[:100]}...")  # Anteprima primo pezzo
            
            return chunk_text, new_index
    except Exception as e:
        logging.error(f"❌ Errore nella lettura del file {file_path}: {e}")
        return None, start_idx

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
    chunk, new_index = read_file_chunk(file_path, schedule.last_sent_index,
                                       schedule.words_per_minute * schedule.minutes_per_reading)

    if not chunk:
        logging.info("⚠️ Nessun altro testo da inviare, fine della lettura.")
        return

    send_email(user.email, f"{book.title} - Nuova lettura", chunk)

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
