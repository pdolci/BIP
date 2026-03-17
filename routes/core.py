import os
import random
import re
import uuid
from functools import wraps

import chardet
from flask import flash, redirect, request, session, url_for
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import or_
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename

from bip_logging import get_logger
from config import Config
from email_sender import DELIVERY_TELEGRAM
from extensions import db
from html_processing import sanitize_uploaded_html
from models import Book, DeliveryEvent
from schedule_utils import FREQ_DAILY, FREQ_EVERY_N_DAYS, FREQ_WEEKDAYS, FREQ_WEEKEND
from time_utils import utc_naive_to_local_naive, utc_now_timestamp

logger = get_logger(__name__)

UPLOAD_FOLDER = Config.UPLOAD_FOLDER
COVER_UPLOAD_FOLDER = Config.COVER_UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {"txt", "html", "htm"}
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
WEEKDAY_CHOICES = {"0", "1", "2", "3", "4", "5", "6"}
MAX_ACTIVE_SUBSCRIPTIONS = 3
ORIGIN_QUOTES_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Origin.txt")

EMAIL_REGEX = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)
PASSWORD_ALLOWED_SYMBOLS = "!£$%&^"
SESSION_LAST_ACTIVITY_KEY = "last_activity_ts"
SESSION_VERSION_KEY = "session_version"
DUMMY_PASSWORD_HASH = generate_password_hash("dummy-password-for-login")


def _is_valid_email(value):
    return bool(EMAIL_REGEX.fullmatch((value or "").strip()))


def _validate_password_requirements(password):
    password = password or ""
    if not 8 <= len(password) <= 15:
        return False
    if not re.search(r"[A-Za-z]", password):
        return False
    if not re.search(r"\d", password):
        return False
    if not re.search(rf"[{re.escape(PASSWORD_ALLOWED_SYMBOLS)}]", password):
        return False
    return bool(re.fullmatch(rf"[A-Za-z\d{re.escape(PASSWORD_ALLOWED_SYMBOLS)}]+", password))


def _password_requirements_message():
    return (
        "La password deve essere lunga tra 8 e 15 caratteri e contenere almeno "
        "una lettera, un numero e un simbolo tra !£$%&^."
    )


def _parse_int_form_field(field_name, *, default=None, min_value=None, max_value=None, label=None):
    raw_value = request.form.get(field_name)
    if raw_value in (None, ""):
        if default is not None:
            value = default
        else:
            raise ValueError(f"Il campo {label or field_name} è obbligatorio.")
    else:
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            raise ValueError(f"Il campo {label or field_name} deve essere un numero intero.")

    if min_value is not None and value < min_value:
        raise ValueError(f"Il campo {label or field_name} deve essere almeno {min_value}.")
    if max_value is not None and value > max_value:
        raise ValueError(f"Il campo {label or field_name} non può superare {max_value}.")
    return value


def _client_ip_address():
    return request.remote_addr or "unknown"


def _rate_limit_value(attempts, window_seconds):
    attempts = max(int(attempts), 1)
    window_seconds = max(int(window_seconds), 1)
    return f"{attempts} per {window_seconds} seconds"


def is_logged_in():
    return "user_id" in session


def has_admin_access():
    return bool(session.get("is_admin"))


def has_book_management_access():
    return bool(session.get("is_admin") or session.get("is_content_manager"))


def require_login(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_logged_in():
            flash("Devi effettuare il login per accedere a questa pagina.")
            return redirect(url_for("app_routes.login"))
        return f(*args, **kwargs)
    return decorated


def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_logged_in() or not has_admin_access():
            flash("Accesso negato!")
            return redirect(url_for("app_routes.index"))
        return f(*args, **kwargs)
    return decorated


def require_book_management(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_logged_in() or not has_book_management_access():
            flash("Accesso negato!")
            return redirect(url_for("app_routes.index"))
        return f(*args, **kwargs)
    return decorated


def get_email_token_serializer():
    return URLSafeTimedSerializer(Config.SECRET_KEY)


def get_reset_token_serializer():
    return URLSafeTimedSerializer(Config.SECRET_KEY)


def _get_random_origin_quote():
    try:
        with open(ORIGIN_QUOTES_FILE, "r", encoding="utf-8", errors="replace") as source:
            quotes = [line.strip() for line in source if line.strip()]
    except OSError as error:
        logger.warning("⚠️ Impossibile leggere Origin.txt: %s", error)
        return None

    return random.choice(quotes) if quotes else None


def _normalize_tags(tags_value):
    return [tag.strip() for tag in (tags_value or "").split(",") if tag.strip()]


def _extract_cover_file_extension(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    filename = secure_filename(file_storage.filename)
    if "." not in filename:
        return None
    extension = filename.rsplit(".", 1)[1].lower()
    return extension if extension in ALLOWED_IMAGE_EXTENSIONS else None


def _sanitize_uploaded_book_file(file_path, extension):
    if extension not in {"html", "htm"}:
        return
    with open(file_path, "rb") as source:
        raw_content = source.read()
    encoding = chardet.detect(raw_content).get("encoding") or "utf-8"
    decoded = raw_content.decode(encoding, errors="replace")
    with open(file_path, "w", encoding="utf-8", errors="replace") as target:
        target.write(sanitize_uploaded_html(decoded))


def _store_cover_on_disk(file_storage):
    extension = _extract_cover_file_extension(file_storage)
    if not extension:
        return None
    cover_filename = f"{uuid.uuid4().hex}.{extension}"
    destination = os.path.join(COVER_UPLOAD_FOLDER, cover_filename)
    file_storage.save(destination)
    return cover_filename


def _delete_local_cover_if_present(cover_image):
    if not cover_image or not cover_image.startswith("covers/"):
        return
    local_path = os.path.join(Config.BASE_DIR, "uploads", cover_image)
    if os.path.exists(local_path):
        os.remove(local_path)


def _build_book_filters(search_query, author, year, genre, tags, language, max_hours, semantic_book_ids=None):
    filters = []
    normalized_query = (search_query or "").strip().lower()

    if author:
        filters.append(Book.author.ilike(f"%{author}%"))
    if year and year.isdigit():
        filters.append(Book.publication_year == int(year))
    if genre:
        filters.append(Book.genre.ilike(f"%{genre}%"))
    if language:
        filters.append(Book.language.ilike(f"%{language}%"))
    if max_hours:
        try:
            filters.append(Book.estimated_reading_hours <= float(max_hours))
        except ValueError:
            pass

    for tag in [token.strip() for token in (tags or "").split(",") if token.strip()]:
        filters.append(Book.tags.ilike(f"%{tag}%"))

    if normalized_query:
        text_match = or_(
            Book.title.ilike(f"%{normalized_query}%"),
            Book.short_description.ilike(f"%{normalized_query}%"),
            Book.author.ilike(f"%{normalized_query}%"),
            Book.genre.ilike(f"%{normalized_query}%"),
            Book.tags.ilike(f"%{normalized_query}%"),
            Book.language.ilike(f"%{normalized_query}%"),
        )
        semantic_match = Book.id.in_(semantic_book_ids) if semantic_book_ids else None
        filters.append(or_(text_match, semantic_match) if semantic_match is not None else text_match)

        hours_match = re.search(r"<\s*(\d+(?:[\.,]\d+)?)\s*ore", normalized_query)
        if hours_match:
            threshold = float(hours_match.group(1).replace(",", "."))
            filters.append(Book.estimated_reading_hours <= threshold)

    return filters


def describe_delivery_channel(schedule):
    return "Telegram" if schedule.delivery_channel == DELIVERY_TELEGRAM else "Email"


def describe_frequency(schedule):
    frequency_type = schedule.frequency_type or FREQ_EVERY_N_DAYS
    if frequency_type == FREQ_DAILY:
        return "Giornaliera"
    if frequency_type == FREQ_WEEKEND:
        return "Weekend"
    if frequency_type == FREQ_WEEKDAYS:
        labels = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
        selected = []
        for token in (schedule.weekdays or "0,1,2,3,4").split(","):
            if token.strip().isdigit():
                idx = int(token)
                if 0 <= idx < len(labels):
                    selected.append(labels[idx])
        return "Giorni: " + ", ".join(selected) if selected else "Giorni specifici"

    days = max(int(schedule.frequency_days or 1), 1)
    return "Ogni giorno" if days == 1 else f"Ogni {days} giorni"


def _count_book_words(schedule):
    if schedule.book and schedule.book.word_count:
        return schedule.book.word_count
    try:
        with open(schedule.book.get_absolute_path(), "r", encoding="utf-8", errors="replace") as source:
            words_count = len(re.findall(r"\S+", source.read()))
            schedule.book.word_count = words_count
            db.session.commit()
            return words_count
    except Exception as error:
        logger.warning("⚠️ Impossibile calcolare le parole per schedule %s: %s", schedule.id, error)
        return 0


def _is_schedule_completed(schedule, total_words=None):
    total_words = _count_book_words(schedule) if total_words is None else total_words
    return total_words > 0 and schedule.last_sent_index >= total_words


def _build_dashboard(active_schedules):
    if not active_schedules:
        return None

    total_words = 0
    total_read = 0
    total_remaining_minutes = 0
    history = []

    schedule_ids = [schedule.id for schedule in active_schedules]
    events = DeliveryEvent.query.filter(DeliveryEvent.schedule_id.in_(schedule_ids)).order_by(
        DeliveryEvent.created_at.desc()
    ).all() if schedule_ids else []

    events_by_schedule = {}
    for event in events:
        bucket = events_by_schedule.setdefault(event.schedule_id, [])
        if len(bucket) < 12:
            bucket.append(event)

    for schedule in active_schedules:
        schedule_total_words = _count_book_words(schedule)
        schedule_read_words = min(schedule.last_sent_index, schedule_total_words) if schedule_total_words else schedule.last_sent_index
        remaining_words = max(schedule_total_words - schedule_read_words, 0)

        total_words += schedule_total_words
        total_read += schedule_read_words

        words_per_session = max(schedule.words_per_minute * schedule.minutes_per_reading, 1)
        remaining_sessions = remaining_words / words_per_session
        total_remaining_minutes += int(round(remaining_sessions * schedule.minutes_per_reading))

        for event in events_by_schedule.get(schedule.id, []):
            history.append({
                "book_title": schedule.book.title,
                "event_type": event.event_type,
                "created_at": event.created_at,
                "note": event.note,
                "words_count": event.words_count,
            })

    history.sort(key=lambda item: item["created_at"], reverse=True)
    completion = int(round((total_read / total_words) * 100)) if total_words else 0
    return {
        "completion": completion,
        "remaining_words": max(total_words - total_read, 0),
        "remaining_minutes": total_remaining_minutes,
        "history": history[:20],
    }


def format_app_datetime(value, fmt="%d/%m/%Y %H:%M", default="n/d"):
    return default if value is None else utc_naive_to_local_naive(value).strftime(fmt)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def update_session_last_activity():
    session[SESSION_LAST_ACTIVITY_KEY] = utc_now_timestamp()
