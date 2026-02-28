import os
import datetime
import re
import random
import threading
from collections import defaultdict, deque
from sqlalchemy import or_, and_
from sqlalchemy.orm import joinedload
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, send_from_directory
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.utils import secure_filename
import uuid
import chardet
from extensions import db
from models import User, Book, ReadingSchedule, DeliveryEvent
from email_sender import (
    DELIVERY_EMAIL,
    DELIVERY_TELEGRAM,
    SUPPORTED_DELIVERY_CHANNELS,
    send_next_book_part,
    send_password_reset_email,
    send_email_confirmation_request,
    count_total_words,
)
from config import Config
from schedule_utils import (
    parse_time_str,
    serialize_weekdays,
    compute_next_send_datetime_from_params,
    compute_next_send_datetime,
    FREQ_EVERY_N_DAYS,
    FREQ_DAILY,
    FREQ_WEEKDAYS,
    FREQ_WEEKEND,
)
import logging
from time_utils import (
    utc_now_naive,
    utc_now_timestamp,
    local_now_naive,
    local_naive_to_utc_naive,
    utc_naive_to_local_naive,
    compute_next_send_utc,
)
from html_processing import sanitize_uploaded_html

UPLOAD_FOLDER = Config.UPLOAD_FOLDER
COVER_UPLOAD_FOLDER = Config.COVER_UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {"txt", "html", "htm"}
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

app_routes = Blueprint("app_routes", __name__)

WEEKDAY_CHOICES = {"0", "1", "2", "3", "4", "5", "6"}
MAX_ACTIVE_SUBSCRIPTIONS = 3
ORIGIN_QUOTES_FILE = os.path.join(os.path.dirname(__file__), "Origin.txt")

EMAIL_REGEX = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)
PASSWORD_ALLOWED_SYMBOLS = "!£$%&^"
SESSION_LAST_ACTIVITY_KEY = "last_activity_ts"
_RATE_LIMIT_STORAGE_LOCK = threading.Lock()
_RATE_LIMIT_STORAGE = defaultdict(deque)


@app_routes.before_app_request
def enforce_session_inactivity_timeout():
    user_id = session.get("user_id")
    if not user_id:
        return None

    endpoint = request.endpoint or ""
    if endpoint in {"app_routes.login", "app_routes.logout", "static"}:
        return None

    timeout_seconds = max(Config.SESSION_INACTIVITY_MINUTES, 1) * 60
    now_ts = utc_now_timestamp()
    last_activity_ts = session.get(SESSION_LAST_ACTIVITY_KEY)

    if isinstance(last_activity_ts, int) and now_ts - last_activity_ts > timeout_seconds:
        session.clear()
        flash("Sessione scaduta per inattività. Effettua nuovamente il login.")
        return redirect(url_for("app_routes.login"))

    session[SESSION_LAST_ACTIVITY_KEY] = now_ts
    return None


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


def _client_ip_address():
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.remote_addr or "unknown"


def _is_rate_limited(bucket_name, max_attempts, window_seconds):
    if max_attempts <= 0 or window_seconds <= 0:
        return False

    client_ip = _client_ip_address()
    bucket_key = f"{bucket_name}:{client_ip}"
    now = utc_now_naive()
    threshold = now - datetime.timedelta(seconds=window_seconds)

    with _RATE_LIMIT_STORAGE_LOCK:
        bucket = _RATE_LIMIT_STORAGE[bucket_key]

        while bucket and bucket[0] < threshold:
            bucket.popleft()

        if len(bucket) >= max_attempts:
            return True

        bucket.append(now)

    return False


def is_logged_in():
    return "user_id" in session


def has_admin_access():
    return bool(session.get("is_admin"))


def has_book_management_access():
    return bool(session.get("is_admin") or session.get("is_content_manager"))


def get_email_token_serializer():
    return URLSafeTimedSerializer(Config.SECRET_KEY)



def _get_random_origin_quote():
    try:
        with open(ORIGIN_QUOTES_FILE, "r", encoding="utf-8", errors="replace") as source:
            quotes = [line.strip() for line in source if line.strip()]
    except OSError as error:
        logging.warning(f"⚠️ Impossibile leggere Origin.txt: {error}")
        return None

    if not quotes:
        return None

    return random.choice(quotes)


def _normalize_tags(tags_value):
    if not tags_value:
        return []
    return [tag.strip() for tag in tags_value.split(",") if tag.strip()]




def _extract_cover_file_extension(file_storage):
    if not file_storage or not file_storage.filename:
        return None

    filename = secure_filename(file_storage.filename)
    if "." not in filename:
        return None

    extension = filename.rsplit(".", 1)[1].lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        return None

    return extension


def _sanitize_uploaded_book_file(file_path, extension):
    if extension not in {"html", "htm"}:
        return

    with open(file_path, "rb") as source:
        raw_content = source.read()

    detected = chardet.detect(raw_content)
    encoding = detected.get("encoding") or "utf-8"
    decoded = raw_content.decode(encoding, errors="replace")
    sanitized = sanitize_uploaded_html(decoded)

    with open(file_path, "w", encoding="utf-8", errors="replace") as target:
        target.write(sanitized)


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

def _build_book_filters(search_query, author, year, genre, tags, language, max_hours):
    filters = []
    normalized_query = (search_query or "").strip().lower()
    tags_tokens = [token.strip() for token in (tags or "").split(",") if token.strip()]

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

    for tag in tags_tokens:
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

        semantic_match = None
        if "classici russi" in normalized_query:
            semantic_match = and_(
                or_(Book.genre.ilike("%classico%"), Book.tags.ilike("%classici%")),
                or_(Book.tags.ilike("%russi%"), Book.language.ilike("%russo%")),
            )
        elif "saggi brevi" in normalized_query:
            semantic_match = and_(
                or_(Book.genre.ilike("%saggio%"), Book.tags.ilike("%saggi%")),
                Book.estimated_reading_hours <= 5,
            )

        filters.append(or_(text_match, semantic_match) if semantic_match is not None else text_match)

        hours_match = re.search(r"<\s*(\d+(?:[\.,]\d+)?)\s*ore", normalized_query)
        if hours_match:
            threshold = float(hours_match.group(1).replace(",", "."))
            filters.append(Book.estimated_reading_hours <= threshold)

    return filters


def describe_delivery_channel(schedule):
    if schedule.delivery_channel == DELIVERY_TELEGRAM:
        return "Telegram"
    return "Email"


def describe_frequency(schedule):
    frequency_type = schedule.frequency_type or FREQ_EVERY_N_DAYS
    if frequency_type == FREQ_DAILY:
        return "Giornaliera"
    if frequency_type == FREQ_WEEKEND:
        return "Weekend"
    if frequency_type == FREQ_WEEKDAYS:
        weekdays = schedule.weekdays or "0,1,2,3,4"
        labels = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
        selected = []
        for token in weekdays.split(","):
            token = token.strip()
            if token.isdigit():
                idx = int(token)
                if 0 <= idx < len(labels):
                    selected.append(labels[idx])
        return "Giorni: " + ", ".join(selected) if selected else "Giorni specifici"

    days = max(int(schedule.frequency_days or 1), 1)
    if days == 1:
        return "Ogni giorno"
    return f"Ogni {days} giorni"


def _count_book_words(schedule):
    """Conta le parole del libro per mostrare metriche di progresso."""
    if schedule.book and schedule.book.word_count:
        return schedule.book.word_count

    try:
        with open(schedule.book.get_absolute_path(), "r", encoding="utf-8", errors="replace") as source:
            words_count = len(re.findall(r"\S+", source.read()))
            schedule.book.word_count = words_count
            return words_count
    except Exception as error:
        logging.warning(f"⚠️ Impossibile calcolare le parole per schedule {schedule.id}: {error}")
        return 0


def _compute_streak(sent_events):
    if not sent_events:
        return 0

    sent_days = sorted({utc_naive_to_local_naive(event.created_at).date() for event in sent_events}, reverse=True)
    streak = 0
    cursor = sent_days[0]
    for day in sent_days:
        if day == cursor:
            streak += 1
            cursor = cursor - datetime.timedelta(days=1)
        elif day < cursor:
            break
    return streak


def _build_dashboard(active_schedules):
    if not active_schedules:
        return None

    total_words = 0
    total_read = 0
    total_remaining_minutes = 0
    sent_events = []
    history = []

    schedule_ids = [schedule.id for schedule in active_schedules]
    events = []
    if schedule_ids:
        events = DeliveryEvent.query.filter(DeliveryEvent.schedule_id.in_(schedule_ids)).order_by(
            DeliveryEvent.created_at.desc()
        ).all()

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

        schedule_events = events_by_schedule.get(schedule.id, [])
        sent_events.extend([event for event in schedule_events if event.event_type == "sent"])

        for event in schedule_events:
            history.append({
                "book_title": schedule.book.title,
                "event_type": event.event_type,
                "created_at": event.created_at,
                "note": event.note,
                "words_count": event.words_count,
            })

    completion = int(round((total_read / total_words) * 100)) if total_words else 0
    remaining_words = max(total_words - total_read, 0)
    history.sort(key=lambda item: item["created_at"], reverse=True)

    return {
        "completion": completion,
        "remaining_words": remaining_words,
        "remaining_minutes": total_remaining_minutes,
        "streak_days": _compute_streak(sent_events),
        "history": history[:20],
    }


def format_app_datetime(value, fmt="%d/%m/%Y %H:%M", default="n/d"):
    if value is None:
        return default
    return utc_naive_to_local_naive(value).strftime(fmt)

def get_reset_token_serializer():
    """Crea il serializer usato per il recupero password."""
    return URLSafeTimedSerializer(Config.SECRET_KEY)

def allowed_file(filename):
    """ Controlla se il file ha un'estensione permessa """
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

@app_routes.route("/")
def index():
    books = Book.query.filter_by(is_active=True).order_by(Book.title.asc()).all()
    random_quote = _get_random_origin_quote()
    return render_template("index.html", books=books, random_quote=random_quote)

@app_routes.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        telegram_handle = (request.form.get("telegram_handle") or "").strip()

        if not _is_valid_email(email):
            flash("Inserisci un indirizzo email valido.")
            return redirect(url_for("app_routes.register"))

        user = User.query.filter_by(email=email).first()
        if user:
            if not user.email_confirmed:
                serializer = get_email_token_serializer()
                token = serializer.dumps(user.email, salt="email-confirm")
                confirmation_url = url_for("app_routes.confirm_email", token=token, _external=True)
                send_email_confirmation_request(user.email, confirmation_url)
                flash("Questa email è già registrata ma non confermata: ti abbiamo inviato un nuovo link di conferma.")
            else:
                flash("Esiste già un account con questa email. Prova ad accedere.")
            return redirect(url_for("app_routes.login"))

        if not _validate_password_requirements(password):
            flash(_password_requirements_message())
            return redirect(url_for("app_routes.register"))

        user = User(email=email, telegram_handle=telegram_handle or None, email_confirmed=False)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        serializer = get_email_token_serializer()
        token = serializer.dumps(user.email, salt="email-confirm")
        confirmation_url = url_for("app_routes.confirm_email", token=token, _external=True)
        send_email_confirmation_request(user.email, confirmation_url)

        flash("Registrazione completata! Ti abbiamo inviato una mail per confermare l'indirizzo.")
        return redirect(url_for("app_routes.login"))
    return render_template("register.html")

@app_routes.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if _is_rate_limited(
            "login",
            Config.LOGIN_RATE_LIMIT_ATTEMPTS,
            Config.LOGIN_RATE_LIMIT_WINDOW_SECONDS,
        ):
            flash("Troppi tentativi di login. Riprova tra qualche minuto.")
            return redirect(url_for("app_routes.login"))

        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            if not user.email_confirmed:
                flash("Conferma prima il tuo indirizzo email tramite il link ricevuto.")
                return redirect(url_for("app_routes.login"))
            session["user_id"] = user.id
            session["is_admin"] = user.is_admin
            session["is_content_manager"] = user.is_content_manager
            session.permanent = True
            session[SESSION_LAST_ACTIVITY_KEY] = utc_now_timestamp()
            flash("Login riuscito!")
            return redirect(url_for("app_routes.index"))
        else:
            flash("Credenziali non valide!")
    return render_template("login.html")

@app_routes.route("/confirm_email/<token>")
def confirm_email(token):
    serializer = get_email_token_serializer()

    try:
        email = serializer.loads(token, salt="email-confirm", max_age=60 * 60 * 24)
    except (SignatureExpired, BadSignature):
        flash("Link di conferma non valido o scaduto.")
        return redirect(url_for("app_routes.login"))

    user = User.query.filter_by(email=email).first()
    if not user:
        flash("Utente non trovato.")
        return redirect(url_for("app_routes.register"))

    if not user.email_confirmed:
        user.email_confirmed = True
        db.session.commit()

    flash("Email confermata con successo! Ora puoi accedere.")
    return redirect(url_for("app_routes.login"))

@app_routes.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        if _is_rate_limited(
            "forgot-password",
            Config.FORGOT_PASSWORD_RATE_LIMIT_ATTEMPTS,
            Config.FORGOT_PASSWORD_RATE_LIMIT_WINDOW_SECONDS,
        ):
            flash("Troppi tentativi di recupero password. Riprova tra qualche minuto.")
            return redirect(url_for("app_routes.forgot_password"))

        email = request.form["email"]
        user = User.query.filter_by(email=email).first()

        if user:
            serializer = get_reset_token_serializer()
            token = serializer.dumps(user.email, salt="password-reset")
            reset_url = url_for("app_routes.reset_password", token=token, _external=True)
            send_password_reset_email(user.email, reset_url)

        flash("Se l'email è registrata, riceverai un link per reimpostare la password.")
        return redirect(url_for("app_routes.login"))

    return render_template("forgot_password.html")

@app_routes.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password(token):
    serializer = get_reset_token_serializer()

    try:
        email = serializer.loads(token, salt="password-reset", max_age=3600)
    except (SignatureExpired, BadSignature):
        flash("Link di recupero non valido o scaduto.")
        return redirect(url_for("app_routes.forgot_password"))

    user = User.query.filter_by(email=email).first()
    if not user:
        flash("Utente non trovato.")
        return redirect(url_for("app_routes.forgot_password"))

    if request.method == "POST":
        password = request.form["password"]
        confirm_password = request.form["confirm_password"]

        if password != confirm_password:
            flash("Le password non coincidono.")
            return redirect(url_for("app_routes.reset_password", token=token))

        if not _validate_password_requirements(password):
            flash(_password_requirements_message())
            return redirect(url_for("app_routes.reset_password", token=token))

        user.set_password(password)
        db.session.commit()
        flash("Password aggiornata con successo! Ora puoi accedere.")
        return redirect(url_for("app_routes.login"))

    return render_template("reset_password.html")

@app_routes.route("/logout")
def logout():
    session.pop("user_id", None)
    session.pop("is_admin", None)
    session.pop("is_content_manager", None)
    session.pop(SESSION_LAST_ACTIVITY_KEY, None)
    flash("Logout effettuato!")
    return redirect(url_for("app_routes.index"))


@app_routes.route("/profile", methods=["GET", "POST"])
def profile():
    if "user_id" not in session:
        flash("Devi effettuare il login per modificare il profilo.")
        return redirect(url_for("app_routes.login"))

    user = User.query.get(session["user_id"])
    if not user:
        flash("Utente non trovato.")
        return redirect(url_for("app_routes.logout"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        telegram_handle = (request.form.get("telegram_handle") or "").strip()
        preferred_delivery_channel = (request.form.get("preferred_delivery_channel") or DELIVERY_EMAIL).strip().lower()
        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        if not email:
            flash("L'email è obbligatoria.")
            return redirect(url_for("app_routes.profile"))

        if not _is_valid_email(email):
            flash("Inserisci un indirizzo email valido.")
            return redirect(url_for("app_routes.profile"))

        existing_user = User.query.filter(User.email == email, User.id != user.id).first()
        if existing_user:
            flash("Questa email è già in uso da un altro account.")
            return redirect(url_for("app_routes.profile"))

        if preferred_delivery_channel not in SUPPORTED_DELIVERY_CHANNELS:
            flash("Canale di consegna preferito non valido.")
            return redirect(url_for("app_routes.profile"))

        if preferred_delivery_channel == DELIVERY_TELEGRAM and not telegram_handle:
            flash("Per usare Telegram come canale predefinito devi inserire il tuo handle.")
            return redirect(url_for("app_routes.profile"))

        if new_password or confirm_password:
            if new_password != confirm_password:
                flash("Le nuove password non coincidono.")
                return redirect(url_for("app_routes.profile"))

            if not _validate_password_requirements(new_password):
                flash(_password_requirements_message())
                return redirect(url_for("app_routes.profile"))

            user.set_password(new_password)

        user.email = email
        user.telegram_handle = telegram_handle or None
        user.preferred_delivery_channel = preferred_delivery_channel

        schedules = ReadingSchedule.query.filter_by(user_id=user.id).all()
        for schedule in schedules:
            schedule.delivery_channel = preferred_delivery_channel

        db.session.commit()
        flash("Profilo aggiornato con successo!")
        return redirect(url_for("app_routes.profile"))

    return render_template(
        "profile.html",
        user=user,
        delivery_email=DELIVERY_EMAIL,
        delivery_telegram=DELIVERY_TELEGRAM,
    )


@app_routes.route("/profile/delete_account", methods=["POST"])
def delete_account():
    if "user_id" not in session:
        flash("Devi effettuare il login.")
        return redirect(url_for("app_routes.login"))

    confirmation = (request.form.get("delete_confirmation") or "").strip().upper()
    if confirmation != "ELIMINA":
        flash("Per cancellare l'account digita ELIMINA nel campo di conferma.")
        return redirect(url_for("app_routes.profile"))

    user = User.query.get(session["user_id"])
    if not user:
        flash("Utente non trovato.")
        return redirect(url_for("app_routes.logout"))

    schedules = ReadingSchedule.query.filter_by(user_id=user.id).all()
    for schedule in schedules:
        DeliveryEvent.query.filter_by(schedule_id=schedule.id).delete(synchronize_session=False)
        db.session.delete(schedule)

    db.session.delete(user)
    db.session.commit()

    session.pop("user_id", None)
    session.pop("is_admin", None)
    session.pop("is_content_manager", None)
    flash("Il tuo account è stato disiscritto e cancellato completamente.")
    return redirect(url_for("app_routes.index"))

@app_routes.route("/select_book", methods=["GET", "POST"])
def select_book():
    if "user_id" not in session:
        flash("Devi effettuare il login per selezionare un libro.")
        return redirect(url_for("app_routes.login"))

    user_id = session["user_id"]
    active_schedules = ReadingSchedule.query.options(joinedload(ReadingSchedule.book)).filter_by(user_id=user_id).all()

    if request.method == "POST":
        book_id = request.form["book_id"]
        minutes_per_reading = int(request.form["minutes_per_reading"])
        words_per_minute = int(request.form.get("words_per_minute", 200))
        frequency_type = request.form.get("frequency_type") or request.form.get("frequency_mode", FREQ_EVERY_N_DAYS)
        if frequency_type == "interval":
            frequency_type = FREQ_EVERY_N_DAYS
        frequency_days = int(request.form.get("frequency_days", 1) or 1)
        delivery_time = parse_time_str(request.form.get("delivery_time"))
        weekdays_selected = request.form.getlist("weekdays") or request.form.getlist("frequency_weekdays")
        current_user = User.query.get(user_id)
        default_channel = (current_user.preferred_delivery_channel if current_user else DELIVERY_EMAIL)
        delivery_channel = (request.form.get("delivery_channel") or default_channel).strip().lower()
        telegram_handle = (request.form.get("telegram_handle") or "").strip()

        if delivery_channel not in SUPPORTED_DELIVERY_CHANNELS:
            flash("Canale di consegna non valido.")
            return redirect(url_for("app_routes.select_book"))

        resolved_telegram_handle = telegram_handle or (current_user.telegram_handle if current_user else "")
        if delivery_channel == DELIVERY_TELEGRAM and not resolved_telegram_handle:
            flash("Inserisci il tuo handle Telegram per ricevere i pezzi su Telegram.")
            return redirect(url_for("app_routes.select_book"))

        if telegram_handle and current_user:
            current_user.telegram_handle = telegram_handle

        active_subscriptions = sum(1 for schedule in active_schedules if not schedule.is_paused)
        if active_subscriptions >= MAX_ACTIVE_SUBSCRIPTIONS:
            flash("Hai raggiunto il limite di 3 sottoscrizioni attive. Metti in pausa o cancella una sottoscrizione prima di aggiungerne un'altra.")
            return redirect(url_for("app_routes.select_book"))

        if frequency_type == FREQ_WEEKDAYS and not weekdays_selected:
            flash("Seleziona almeno un giorno della settimana.")
            return redirect(url_for("app_routes.select_book"))

        if frequency_type != FREQ_EVERY_N_DAYS:
            frequency_days = 1

        weekdays = None
        if frequency_type == FREQ_WEEKDAYS:
            weekdays = serialize_weekdays(
                [int(day) for day in weekdays_selected if day.isdigit()]
            )
        elif frequency_type == FREQ_WEEKEND:
            weekdays = serialize_weekdays([5, 6])

        now = local_now_naive()
        next_send_date = compute_next_send_datetime_from_params(
            now,
            frequency_type,
            frequency_days,
            weekdays,
            delivery_time,
            allow_immediate=True,
        )

        schedule = ReadingSchedule(
            user_id=user_id,
            book_id=book_id,
            words_per_minute=words_per_minute,
            minutes_per_reading=minutes_per_reading,
            frequency_type=frequency_type,
            frequency_days=frequency_days,
            frequency_mode=frequency_type,
            frequency_weekdays=weekdays,
            weekdays=weekdays,
            delivery_time=delivery_time,
            next_send_date=local_naive_to_utc_naive(next_send_date),
            is_paused=False,
            delivery_channel=delivery_channel,
        )
        db.session.add(schedule)
        db.session.commit()
        flash("Programma di lettura impostato!")
        return redirect(url_for("app_routes.select_book"))

    search_query = request.args.get("q", "").strip()
    filter_author = request.args.get("author", "").strip()
    filter_year = request.args.get("year", "").strip()
    filter_genre = request.args.get("genre", "").strip()
    filter_tags = request.args.get("tags", "").strip()
    filter_language = request.args.get("language", "").strip()
    filter_max_hours = request.args.get("max_hours", "").strip()
    selected_book_id = request.args.get("selected_book_id", type=int)

    filters = _build_book_filters(
        search_query,
        filter_author,
        filter_year,
        filter_genre,
        filter_tags,
        filter_language,
        filter_max_hours,
    )
    books_query = Book.query
    if filters:
        books_query = books_query.filter(*filters)
    books = books_query.order_by(Book.title.asc()).all()
    selected_book = next((book for book in books if book.id == selected_book_id), None)
    if not selected_book and books:
        selected_book = books[0]

    dashboard = _build_dashboard(active_schedules)
    user = User.query.get(user_id)

    return render_template(
        "select_book.html",
        books=books,
        active_schedules=active_schedules,
        dashboard=dashboard,
        describe_frequency=describe_frequency,
        describe_delivery_channel=describe_delivery_channel,
        search_query=search_query,
        filter_author=filter_author,
        filter_year=filter_year,
        filter_genre=filter_genre,
        filter_tags=filter_tags,
        filter_language=filter_language,
        filter_max_hours=filter_max_hours,
        selected_book=selected_book,
        normalize_tags=_normalize_tags,
        delivery_email=DELIVERY_EMAIL,
        delivery_telegram=DELIVERY_TELEGRAM,
        session_telegram_handle=(user.telegram_handle if user else ""),
        default_delivery_channel=(user.preferred_delivery_channel if user else DELIVERY_EMAIL),
    )


@app_routes.route("/reading_center")
def reading_center():
    if "user_id" not in session:
        flash("Devi effettuare il login per accedere al centro di controllo lettura.")
        return redirect(url_for("app_routes.login"))

    user_id = session["user_id"]
    active_schedules = ReadingSchedule.query.options(joinedload(ReadingSchedule.book)).filter_by(user_id=user_id).all()
    dashboard = _build_dashboard(active_schedules)

    return render_template(
        "reading_center.html",
        active_schedules=active_schedules,
        dashboard=dashboard,
        describe_frequency=describe_frequency,
        describe_delivery_channel=describe_delivery_channel,
        format_app_datetime=format_app_datetime,
    )


@app_routes.route("/snooze_schedule/<int:schedule_id>", methods=["POST"])
def snooze_schedule(schedule_id):
    if "user_id" not in session:
        flash("Devi effettuare il login per modificare la tua sottoscrizione.")
        return redirect(url_for("app_routes.login"))

    schedule = ReadingSchedule.query.get(schedule_id)
    if not schedule or schedule.user_id != session["user_id"]:
        flash("Operazione non consentita.")
        return redirect(url_for("app_routes.select_book"))

    action = request.form.get("action")
    now = utc_now_naive()

    if action == "skip_next":
        schedule.skip_next = True
        schedule.snooze_until = None
        flash("La prossima consegna sarà saltata.")
    elif action == "delay_24h":
        base_time = schedule.next_send_date if schedule.next_send_date and schedule.next_send_date > now else now
        schedule.snooze_until = base_time + datetime.timedelta(hours=24)
        schedule.next_send_date = schedule.snooze_until
        schedule.skip_next = False
        flash("Consegna rimandata di 24 ore.")
    else:
        flash("Azione di snooze non valida.")

    db.session.commit()
    return redirect(url_for("app_routes.select_book"))


@app_routes.route("/travel_mode/<int:schedule_id>", methods=["POST"])
def travel_mode(schedule_id):
    if "user_id" not in session:
        flash("Devi effettuare il login per modificare la tua sottoscrizione.")
        return redirect(url_for("app_routes.login"))

    schedule = ReadingSchedule.query.get(schedule_id)
    if not schedule or schedule.user_id != session["user_id"]:
        flash("Operazione non consentita.")
        return redirect(url_for("app_routes.select_book"))

    action = request.form.get("action")
    now = utc_now_naive()

    if action == "clear":
        schedule.travel_pause_until = None
        flash("Modalità viaggio disattivata.")
        db.session.commit()
        return redirect(url_for("app_routes.select_book"))

    resume_date_raw = request.form.get("resume_date")
    resume_time_raw = request.form.get("resume_time")

    if not resume_date_raw:
        flash("Seleziona una data di ripartenza.")
        return redirect(url_for("app_routes.select_book"))

    try:
        resume_date = datetime.date.fromisoformat(resume_date_raw)
    except ValueError:
        flash("Data di ripartenza non valida.")
        return redirect(url_for("app_routes.select_book"))

    resume_time = parse_time_str(resume_time_raw)
    if resume_time is None:
        resume_time = schedule.delivery_time or datetime.time(9, 0)

    resume_at_local = datetime.datetime.combine(resume_date, resume_time)
    resume_at = local_naive_to_utc_naive(resume_at_local)

    if resume_at <= now:
        flash("La ripartenza deve essere nel futuro.")
        return redirect(url_for("app_routes.select_book"))

    schedule.travel_pause_until = resume_at
    if schedule.next_send_date is None or schedule.next_send_date < resume_at:
        schedule.next_send_date = resume_at

    db.session.commit()
    flash("Modalità viaggio attivata.")
    return redirect(url_for("app_routes.select_book"))

@app_routes.route("/pause_schedule/<int:schedule_id>", methods=["POST"])
def pause_schedule(schedule_id):
    if "user_id" not in session:
        flash("Devi effettuare il login per modificare la tua sottoscrizione.")
        return redirect(url_for("app_routes.login"))

    schedule = ReadingSchedule.query.get(schedule_id)
    
    if schedule and schedule.user_id == session["user_id"]:
        schedule.is_paused = not schedule.is_paused  # Toggle status
        db.session.add(DeliveryEvent(
            schedule_id=schedule.id,
            event_type="paused" if schedule.is_paused else "resumed",
            words_count=0,
            note="Pausa attivata" if schedule.is_paused else "Lettura ripresa",
        ))
        db.session.commit()
        flash("Sottoscrizione aggiornata con successo!")
    else:
        flash("Operazione non consentita.")

    return redirect(url_for("app_routes.select_book"))


@app_routes.route("/snooze_next_schedule/<int:schedule_id>", methods=["POST"])
def snooze_next_schedule(schedule_id):
    if "user_id" not in session:
        flash("Devi effettuare il login per modificare la tua sottoscrizione.")
        return redirect(url_for("app_routes.login"))

    schedule = ReadingSchedule.query.get(schedule_id)
    if not schedule or schedule.user_id != session["user_id"]:
        flash("Operazione non consentita.")
        return redirect(url_for("app_routes.select_book"))

    now = utc_now_naive()
    reference = max(schedule.next_send_date, now)
    schedule.next_send_date = compute_next_send_utc(schedule, reference, allow_immediate=False)
    db.session.add(DeliveryEvent(
        schedule_id=schedule.id,
        event_type="skipped",
        words_count=0,
        note="Salto prossima consegna",
    ))
    db.session.commit()
    flash("Prossima consegna saltata con successo.")
    return redirect(url_for("app_routes.select_book"))


@app_routes.route("/snooze_24h_schedule/<int:schedule_id>", methods=["POST"])
def snooze_24h_schedule(schedule_id):
    if "user_id" not in session:
        flash("Devi effettuare il login per modificare la tua sottoscrizione.")
        return redirect(url_for("app_routes.login"))

    schedule = ReadingSchedule.query.get(schedule_id)
    if not schedule or schedule.user_id != session["user_id"]:
        flash("Operazione non consentita.")
        return redirect(url_for("app_routes.select_book"))

    now = utc_now_naive()
    schedule.next_send_date = max(schedule.next_send_date, now) + datetime.timedelta(hours=24)
    db.session.add(DeliveryEvent(
        schedule_id=schedule.id,
        event_type="skipped",
        words_count=0,
        note="Rimandata di 24 ore",
    ))
    db.session.commit()
    flash("Consegna rimandata di 24 ore.")
    return redirect(url_for("app_routes.select_book"))


@app_routes.route("/travel_mode_schedule/<int:schedule_id>", methods=["POST"])
def travel_mode_schedule(schedule_id):
    if "user_id" not in session:
        flash("Devi effettuare il login per modificare la tua sottoscrizione.")
        return redirect(url_for("app_routes.login"))

    schedule = ReadingSchedule.query.get(schedule_id)
    if not schedule or schedule.user_id != session["user_id"]:
        flash("Operazione non consentita.")
        return redirect(url_for("app_routes.select_book"))

    travel_days = int(request.form.get("travel_days", 0) or 0)
    if travel_days <= 0:
        flash("Inserisci un numero di giorni valido per la modalità viaggio.")
        return redirect(url_for("app_routes.select_book"))

    now = utc_now_naive()
    schedule.travel_pause_until = now + datetime.timedelta(days=travel_days)
    if schedule.next_send_date < schedule.travel_pause_until:
        schedule.next_send_date = schedule.travel_pause_until
    db.session.add(DeliveryEvent(
        schedule_id=schedule.id,
        event_type="skipped",
        words_count=0,
        note=f"Modalità viaggio ({travel_days} giorni)",
    ))
    db.session.commit()
    flash(f"Modalità viaggio attivata per {travel_days} giorni.")
    return redirect(url_for("app_routes.select_book"))

@app_routes.route("/delete_schedule/<int:schedule_id>", methods=["POST"])
def delete_schedule(schedule_id):
    if "user_id" not in session:
        flash("Devi effettuare il login per cancellare la tua sottoscrizione.")
        return redirect(url_for("app_routes.login"))

    schedule = ReadingSchedule.query.get(schedule_id)

    if schedule and schedule.user_id == session["user_id"]:
        DeliveryEvent.query.filter_by(schedule_id=schedule.id).delete(synchronize_session=False)
        db.session.delete(schedule)
        db.session.commit()
        flash("Sottoscrizione eliminata con successo!")
    else:
        flash("Operazione non consentita.")

    return redirect(url_for("app_routes.select_book"))


@app_routes.route("/manage_books")
def manage_books():
    if not is_logged_in() or not has_book_management_access():
        flash("Accesso negato!")
        return redirect(url_for("app_routes.index"))

    books = Book.query.order_by(Book.title.asc()).all()
    return render_template("admin_books.html", books=books)


@app_routes.route("/admin/maintenance")
def admin_maintenance():
    if not is_logged_in() or not has_admin_access():
        flash("Accesso negato!")
        return redirect(url_for("app_routes.index"))

    users = User.query.options(
        joinedload(User.schedules).joinedload(ReadingSchedule.book)
    ).order_by(User.created_at.desc()).all()

    all_schedules = [schedule for user in users for schedule in user.schedules]
    now = utc_now_naive()
    due_schedules = [
        schedule for schedule in all_schedules
        if not schedule.is_paused and schedule.next_send_date and schedule.next_send_date <= now
    ]
    paused_schedules = [schedule for schedule in all_schedules if schedule.is_paused]

    return render_template(
        "admin_maintenance.html",
        users=users,
        now=now,
        total_users=len(users),
        total_schedules=len(all_schedules),
        active_schedules=len(all_schedules) - len(paused_schedules),
        paused_schedules=len(paused_schedules),
        due_schedules=len(due_schedules),
        describe_frequency=describe_frequency,
        describe_delivery_channel=describe_delivery_channel,
        format_app_datetime=format_app_datetime,
    )


@app_routes.route("/admin/schedule/toggle-pause/<int:schedule_id>", methods=["POST"])
def admin_toggle_pause_schedule(schedule_id):
    if not is_logged_in() or not has_admin_access():
        flash("Accesso negato!")
        return redirect(url_for("app_routes.index"))

    schedule = ReadingSchedule.query.get(schedule_id)
    if not schedule:
        flash("Schedulazione non trovata.")
        return redirect(url_for("app_routes.admin_maintenance"))

    schedule.is_paused = not schedule.is_paused
    db.session.commit()
    flash("Schedulazione messa in pausa." if schedule.is_paused else "Schedulazione riattivata.")
    return redirect(url_for("app_routes.admin_maintenance"))


@app_routes.route("/admin/schedule/recompute/<int:schedule_id>", methods=["POST"])
def admin_recompute_schedule(schedule_id):
    if not is_logged_in() or not has_admin_access():
        flash("Accesso negato!")
        return redirect(url_for("app_routes.index"))

    schedule = ReadingSchedule.query.get(schedule_id)
    if not schedule:
        flash("Schedulazione non trovata.")
        return redirect(url_for("app_routes.admin_maintenance"))

    next_local_date = compute_next_send_datetime(
        local_now_naive(),
        schedule,
        allow_immediate=False,
    )
    schedule.next_send_date = local_naive_to_utc_naive(next_local_date)
    db.session.commit()
    flash("Prossimo invio ricalcolato con successo.")
    return redirect(url_for("app_routes.admin_maintenance"))


@app_routes.route("/admin/schedule/reset-progress/<int:schedule_id>", methods=["POST"])
def admin_reset_schedule_progress(schedule_id):
    if not is_logged_in() or not has_admin_access():
        flash("Accesso negato!")
        return redirect(url_for("app_routes.index"))

    schedule = ReadingSchedule.query.get(schedule_id)
    if not schedule:
        flash("Schedulazione non trovata.")
        return redirect(url_for("app_routes.admin_maintenance"))

    schedule.last_sent_index = 0
    schedule.skip_next = False
    schedule.snooze_until = None
    schedule.travel_pause_until = None
    db.session.add(DeliveryEvent(
        schedule_id=schedule.id,
        event_type="skipped",
        words_count=0,
        note="Reset progresso eseguito da admin",
    ))
    db.session.commit()
    flash("Progresso della schedulazione azzerato.")
    return redirect(url_for("app_routes.admin_maintenance"))


@app_routes.route("/admin/user/reset-password/<int:user_id>", methods=["POST"])
def admin_reset_user_password(user_id):
    if not is_logged_in() or not has_admin_access():
        flash("Accesso negato!")
        return redirect(url_for("app_routes.index"))

    user = User.query.get(user_id)
    if not user:
        flash("Utente non trovato.")
        return redirect(url_for("app_routes.admin_maintenance"))

    new_password = (request.form.get("new_password") or "").strip()
    if not new_password:
        flash("Inserisci una nuova password valida.")
        return redirect(url_for("app_routes.admin_maintenance"))

    if not _validate_password_requirements(new_password):
        flash(_password_requirements_message())
        return redirect(url_for("app_routes.admin_maintenance"))

    user.set_password(new_password)
    db.session.commit()
    flash(f"Password aggiornata per {user.email}.")
    return redirect(url_for("app_routes.admin_maintenance"))


@app_routes.route("/admin/user/delete/<int:user_id>", methods=["POST"])
def admin_delete_user(user_id):
    if not is_logged_in() or not has_admin_access():
        flash("Accesso negato!")
        return redirect(url_for("app_routes.index"))

    if session.get("user_id") == user_id:
        flash("Non puoi cancellare il tuo account amministratore da questa schermata.")
        return redirect(url_for("app_routes.admin_maintenance"))

    user = User.query.get(user_id)
    if not user:
        flash("Utente non trovato.")
        return redirect(url_for("app_routes.admin_maintenance"))

    schedules = ReadingSchedule.query.filter_by(user_id=user.id).all()
    for schedule in schedules:
        DeliveryEvent.query.filter_by(schedule_id=schedule.id).delete(synchronize_session=False)
        db.session.delete(schedule)

    db.session.delete(user)
    db.session.commit()
    flash(f"Utente {user.email} eliminato con successo.")
    return redirect(url_for("app_routes.admin_maintenance"))

@app_routes.route("/upload_book", methods=["POST"])
def upload_book():
    if not is_logged_in() or not has_book_management_access():
        flash("Accesso negato!")
        return redirect(url_for("app_routes.index"))

    title = (request.form.get("title") or "").strip()
    file = request.files.get("book_file")

    if not title or not file or not file.filename:
        flash("Inserisci obbligatoriamente il nome del libro e il file da caricare.")
        return redirect(url_for("app_routes.manage_books"))

    short_description = request.form.get("short_description")
    author = request.form.get("author")
    publication_year = request.form.get("publication_year")
    genre = request.form.get("genre")
    tags = request.form.get("tags")
    language = request.form.get("language")
    estimated_reading_hours = request.form.get("estimated_reading_hours")
    cover_image = (request.form.get("cover_image") or "").strip() or None
    cover_image_file = request.files.get("cover_image_file")

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        
        try:
            file.save(file_path)  # ✅ Save the file
            extension = filename.rsplit(".", 1)[1].lower() if "." in filename else ""
            _sanitize_uploaded_book_file(file_path, extension)

            if not os.path.isfile(file_path):
                logging.error(f"❌ File non trovato dopo il salvataggio: {file_path}")
                flash("Errore nel caricamento: inserisci il nome del libro e un file valido.")
                return redirect(url_for("app_routes.manage_books"))

            logging.info(f"✅ Libro salvato: {file_path}")
        except Exception as e:
            logging.error(f"❌ Errore durante il salvataggio/sanitizzazione del file: {e}")
            flash("Errore nel caricamento: inserisci il nome del libro e un file valido.")
            return redirect(url_for("app_routes.manage_books"))

        # Save book in database
        parsed_publication_year = int(publication_year) if publication_year and publication_year.isdigit() else None
        try:
            parsed_estimated_hours = float(estimated_reading_hours) if estimated_reading_hours else None
        except ValueError:
            parsed_estimated_hours = None

        calculated_word_count = count_total_words(file_path)

        cover_filename = _store_cover_on_disk(cover_image_file) if cover_image_file and cover_image_file.filename else None
        if cover_image_file and cover_image_file.filename and not cover_filename:
            flash("Formato copertina non supportato. Usa PNG, JPG, GIF o WEBP.")
            return redirect(url_for("app_routes.manage_books"))

        new_book = Book(
            title=title,
            file_path=filename,
            is_active=True,
            short_description=short_description,
            author=author,
            publication_year=parsed_publication_year,
            genre=genre,
            tags=tags,
            language=language,
            estimated_reading_hours=parsed_estimated_hours,
            cover_image=f"covers/{cover_filename}" if cover_filename else cover_image,
            word_count=calculated_word_count,
        )
        db.session.add(new_book)
        db.session.commit()
        flash("Libro caricato con successo!")
    else:
        flash("Formato non supportato. Carica file .txt, .html o .htm.")

    return redirect(url_for("app_routes.manage_books"))


@app_routes.route("/admin/book/edit/<int:book_id>", methods=["POST"])
def edit_book(book_id):
    if not is_logged_in() or not has_book_management_access():
        flash("Accesso negato!")
        return redirect(url_for("app_routes.index"))

    book = Book.query.get(book_id)
    if not book:
        flash("Libro non trovato.")
        return redirect(url_for("app_routes.manage_books"))

    title = (request.form.get("title") or "").strip()
    if not title:
        flash("Il titolo è obbligatorio.")
        return redirect(url_for("app_routes.manage_books"))

    publication_year = (request.form.get("publication_year") or "").strip()
    estimated_reading_hours = (request.form.get("estimated_reading_hours") or "").strip()

    parsed_publication_year = int(publication_year) if publication_year.isdigit() else None
    try:
        parsed_estimated_hours = float(estimated_reading_hours) if estimated_reading_hours else None
    except ValueError:
        parsed_estimated_hours = None

    book.title = title
    book.short_description = (request.form.get("short_description") or "").strip() or None
    book.author = (request.form.get("author") or "").strip() or None
    book.publication_year = parsed_publication_year
    book.genre = (request.form.get("genre") or "").strip() or None
    book.tags = (request.form.get("tags") or "").strip() or None
    book.language = (request.form.get("language") or "").strip() or None
    book.estimated_reading_hours = parsed_estimated_hours
    cover_image = (request.form.get("cover_image") or "").strip() or None
    cover_image_file = request.files.get("cover_image_file")
    cover_filename = _store_cover_on_disk(cover_image_file) if cover_image_file and cover_image_file.filename else None

    if cover_image_file and cover_image_file.filename and not cover_filename:
        flash("Formato copertina non supportato. Usa PNG, JPG, GIF o WEBP.")
        return redirect(url_for("app_routes.manage_books"))

    if cover_filename:
        _delete_local_cover_if_present(book.cover_image)
        book.cover_image = f"covers/{cover_filename}"
    elif cover_image:
        _delete_local_cover_if_present(book.cover_image)
        book.cover_image = cover_image

    file_path = book.get_absolute_path()
    if os.path.exists(file_path):
        book.word_count = count_total_words(file_path)

    db.session.commit()
    flash("Libro aggiornato con successo!")
    return redirect(url_for("app_routes.manage_books"))


@app_routes.route("/uploads/books/<filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app_routes.route("/uploads/covers/<filename>")
def uploaded_cover(filename):
    return send_from_directory(COVER_UPLOAD_FOLDER, filename)


@app_routes.route("/book_cover/<int:book_id>")
def book_cover(book_id):
    book = Book.query.get_or_404(book_id)
    if not book.cover_image:
        return redirect("https://images.unsplash.com/photo-1512820790803-83ca734da794?auto=format&fit=crop&w=800&q=80")

    if book.cover_image.startswith("covers/"):
        cover_filename = book.cover_image.split("/", 1)[1]
        return redirect(url_for("app_routes.uploaded_cover", filename=cover_filename))

    return redirect(book.cover_image)

@app_routes.route("/admin/book/delete/<int:book_id>", methods=["POST"])
def delete_book(book_id):
    if not is_logged_in() or not has_book_management_access():
        flash("Accesso negato!")
        return redirect(url_for("app_routes.index"))

    book = Book.query.get(book_id)
    if book:
        try:
            db.session.delete(book)
            db.session.commit()
            flash("Libro eliminato!")
        except Exception as e:
            flash(f"Errore durante l'eliminazione del file: {e}")

    return redirect(url_for("app_routes.manage_books"))

@app_routes.route("/admin/book/toggle/<int:book_id>", methods=["POST"])
def toggle_book_status(book_id):
    if not is_logged_in() or not has_book_management_access():
        flash("Accesso negato!")
        return redirect(url_for("app_routes.index"))

    book = Book.query.get(book_id)
    if book:
        book.is_active = not book.is_active
        db.session.commit()
        flash("Stato del libro aggiornato!")

    return redirect(url_for("app_routes.manage_books"))

@app_routes.route("/test_email_sending/<int:schedule_id>")
def test_email_sending(schedule_id):
    if not is_logged_in() or not has_book_management_access():
        flash("Accesso negato!")
        return redirect(url_for("app_routes.index"))

    send_next_book_part(schedule_id)
    flash("Email inviata con successo!")
    return redirect(url_for("app_routes.manage_books"))
