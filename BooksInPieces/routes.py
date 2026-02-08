import os
import datetime
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, send_from_directory
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.utils import secure_filename
from extensions import db
from models import User, Book, ReadingSchedule
from email_sender import send_next_book_part, send_password_reset_email
from config import Config
from schedule_utils import (
    parse_time_str,
    serialize_weekdays,
    compute_next_send_datetime_from_params,
    FREQ_EVERY_N_DAYS,
    FREQ_DAILY,
    FREQ_WEEKDAYS,
    FREQ_WEEKEND,
)
import logging

UPLOAD_FOLDER = Config.UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {"txt", "html", "htm"}

app_routes = Blueprint("app_routes", __name__)

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
        frequency_type = request.form.get("frequency_type", FREQ_EVERY_N_DAYS)
        frequency_days = int(request.form.get("frequency_days", 1))
        delivery_time = parse_time_str(request.form.get("delivery_time"))
        weekdays_selected = request.form.getlist("weekdays")

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

        now = datetime.datetime.utcnow()
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
            weekdays=weekdays,
            delivery_time=delivery_time,
            next_send_date=next_send_date,
            is_paused=False
        )
        db.session.add(schedule)
        db.session.commit()
        flash("Programma di lettura impostato!")
        return redirect(url_for("app_routes.select_book"))

    books = Book.query.all()
    return render_template("select_book.html", books=books, active_schedules=active_schedules)


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
    now = datetime.datetime.utcnow()

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
    now = datetime.datetime.utcnow()

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

    resume_at = datetime.datetime.combine(resume_date, resume_time)

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
        db.session.commit()
        flash("Sottoscrizione aggiornata con successo!")
    else:
        flash("Operazione non consentita.")

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
