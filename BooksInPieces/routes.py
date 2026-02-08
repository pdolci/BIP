import os
import datetime
import re
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, send_from_directory
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.utils import secure_filename
from extensions import db
from models import User, Book, ReadingSchedule, DeliveryEvent
from email_sender import send_next_book_part, send_password_reset_email
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
from time_utils import utc_now_naive, local_now_naive, local_naive_to_utc_naive

UPLOAD_FOLDER = Config.UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {"txt", "html", "htm"}

app_routes = Blueprint("app_routes", __name__)

WEEKDAY_CHOICES = {"0", "1", "2", "3", "4", "5", "6"}


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
    try:
        with open(schedule.book.get_absolute_path(), "r", encoding="utf-8", errors="replace") as source:
            return len(re.findall(r"\S+", source.read()))
    except Exception as error:
        logging.warning(f"⚠️ Impossibile calcolare le parole per schedule {schedule.id}: {error}")
        return 0


def _compute_streak(sent_events):
    if not sent_events:
        return 0

    sent_days = sorted({event.created_at.date() for event in sent_events}, reverse=True)
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

    for schedule in active_schedules:
        schedule_total_words = _count_book_words(schedule)
        schedule_read_words = min(schedule.last_sent_index, schedule_total_words) if schedule_total_words else schedule.last_sent_index
        remaining_words = max(schedule_total_words - schedule_read_words, 0)

        total_words += schedule_total_words
        total_read += schedule_read_words

        words_per_session = max(schedule.words_per_minute * schedule.minutes_per_reading, 1)
        remaining_sessions = remaining_words / words_per_session
        total_remaining_minutes += int(round(remaining_sessions * schedule.minutes_per_reading))

        schedule_events = DeliveryEvent.query.filter_by(schedule_id=schedule.id).order_by(DeliveryEvent.created_at.desc()).limit(12).all()
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

def get_reset_token_serializer():
    """Crea il serializer usato per il recupero password."""
    return URLSafeTimedSerializer(Config.SECRET_KEY)

def allowed_file(filename):
    """ Controlla se il file ha un'estensione permessa """
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def ensure_uploads_folder():
    """ Crea la cartella uploads/books se non esiste """
    try:
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    except Exception as e:
        logging.error(f"❌ Errore nella creazione della cartella: {e}")

@app_routes.route("/")
def index():
    books = Book.query.filter_by(is_active=True).all()
    return render_template("index.html", books=books)

@app_routes.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash("Registrazione completata!")
        return redirect(url_for("app_routes.index"))
    return render_template("register.html")

@app_routes.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            session["user_id"] = user.id
            session["is_admin"] = user.is_admin
            flash("Login riuscito!")
            return redirect(url_for("app_routes.index"))
        else:
            flash("Credenziali non valide!")
    return render_template("login.html")

@app_routes.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
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

        user.set_password(password)
        db.session.commit()
        flash("Password aggiornata con successo! Ora puoi accedere.")
        return redirect(url_for("app_routes.login"))

    return render_template("reset_password.html")

@app_routes.route("/logout")
def logout():
    session.pop("user_id", None)
    session.pop("is_admin", None)
    flash("Logout effettuato!")
    return redirect(url_for("app_routes.index"))

@app_routes.route("/select_book", methods=["GET", "POST"])
def select_book():
    if "user_id" not in session:
        flash("Devi effettuare il login per selezionare un libro.")
        return redirect(url_for("app_routes.login"))

    user_id = session["user_id"]
    active_schedules = ReadingSchedule.query.filter_by(user_id=user_id).all()

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

        if any(not schedule.is_paused for schedule in active_schedules):
            flash("Hai già una sottoscrizione attiva. Mettila in pausa o cancellala prima di aggiungerne un'altra.")
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
            is_paused=False
        )
        db.session.add(schedule)
        db.session.commit()
        flash("Programma di lettura impostato!")
        return redirect(url_for("app_routes.select_book"))

    books = Book.query.all()
    dashboard = _build_dashboard(active_schedules)
    return render_template(
        "select_book.html",
        books=books,
        active_schedules=active_schedules,
        dashboard=dashboard,
        describe_frequency=describe_frequency,
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
    schedule.next_send_date = compute_next_send_datetime(reference, schedule, allow_immediate=False)
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
        db.session.delete(schedule)
        db.session.commit()
        flash("Sottoscrizione eliminata con successo!")
    else:
        flash("Operazione non consentita.")

    return redirect(url_for("app_routes.select_book"))


@app_routes.route("/manage_books")
def manage_books():
    books = Book.query.all()
    return render_template("admin_books.html", books=books)

@app_routes.route("/upload_book", methods=["POST"])
def upload_book():
    if "book_file" not in request.files:
        flash("Nessun file selezionato!")
        return redirect(request.url)

    file = request.files["book_file"]
    title = request.form.get("title")
    short_description = request.form.get("short_description")

    if file.filename == "":
        flash("Nessun file scelto!")
        return redirect(request.url)

    if file and allowed_file(file.filename):
        ensure_uploads_folder()  # ✅ Ensure the directory exists before saving

        filename = secure_filename(file.filename)
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        
        try:
            file.save(file_path)  # ✅ Save the file
            logging.info(f"✅ Libro salvato: {file_path}")
        except Exception as e:
            logging.error(f"❌ Errore durante il salvataggio del file: {e}")
            flash("Errore nel salvataggio del file!")
            return redirect(request.url)

        # Save book in database
        new_book = Book(title=title, file_path=filename, is_active=True, short_description=short_description)
        db.session.add(new_book)
        db.session.commit()
        flash("Libro caricato con successo!")

    return redirect(url_for("app_routes.manage_books"))


@app_routes.route("/uploads/books/<filename>")
def uploaded_file(filename):
    ensure_uploads_folder()
    return send_from_directory(UPLOAD_FOLDER, filename)

@app_routes.route("/admin/book/delete/<int:book_id>", methods=["POST"])
def delete_book(book_id):
    if "user_id" not in session or not session.get("is_admin"):
        flash("Accesso negato!")
        return redirect(url_for("app_routes.index"))

    book = Book.query.get(book_id)
    if book:
        file_path = os.path.join(UPLOAD_FOLDER, book.file_path)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
            db.session.delete(book)
            db.session.commit()
            flash("Libro eliminato!")
        except Exception as e:
            flash(f"Errore durante l'eliminazione del file: {e}")

    return redirect(url_for("app_routes.manage_books"))

@app_routes.route("/admin/book/toggle/<int:book_id>", methods=["POST"])
def toggle_book_status(book_id):
    if "user_id" not in session or not session.get("is_admin"):
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
    if "user_id" not in session or not session.get("is_admin"):
        flash("Accesso negato!")
        return redirect(url_for("app_routes.index"))

    send_next_book_part(schedule_id)
    flash("Email inviata con successo!")
    return redirect(url_for("app_routes.manage_books"))
