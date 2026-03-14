from flask import flash, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired
from werkzeug.security import check_password_hash

from config import Config
from email_sender import send_email_confirmation_request, send_password_reset_email
from extensions import db, limiter
from models import User
from time_utils import utc_now_timestamp

from . import app_routes
from .core import (
    DUMMY_PASSWORD_HASH,
    SESSION_LAST_ACTIVITY_KEY,
    SESSION_VERSION_KEY,
    _client_ip_address,
    _is_valid_email,
    _password_requirements_message,
    _rate_limit_value,
    _validate_password_requirements,
    get_email_token_serializer,
    get_reset_token_serializer,
)


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
                token = get_email_token_serializer().dumps(user.email, salt="email-confirm")
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

        token = get_email_token_serializer().dumps(user.email, salt="email-confirm")
        confirmation_url = url_for("app_routes.confirm_email", token=token, _external=True)
        send_email_confirmation_request(user.email, confirmation_url)

        flash("Registrazione completata! Ti abbiamo inviato una mail per confermare l'indirizzo.")
        return redirect(url_for("app_routes.login"))
    return render_template("register.html")


@app_routes.route("/login", methods=["GET", "POST"])
@limiter.limit(
    lambda: _rate_limit_value(Config.LOGIN_RATE_LIMIT_ATTEMPTS, Config.LOGIN_RATE_LIMIT_WINDOW_SECONDS),
    methods=["POST"],
    key_func=_client_ip_address,
)
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first()
        password_is_valid = user.check_password(password) if user else False
        if not user:
            check_password_hash(DUMMY_PASSWORD_HASH, password)

        if user and password_is_valid:
            if not user.email_confirmed:
                flash("Conferma prima il tuo indirizzo email tramite il link ricevuto.")
                return redirect(url_for("app_routes.login"))
            session["user_id"] = user.id
            session["is_admin"] = user.is_admin
            session["is_content_manager"] = user.is_content_manager
            session.permanent = True
            session[SESSION_LAST_ACTIVITY_KEY] = utc_now_timestamp()
            session[SESSION_VERSION_KEY] = user.session_version
            flash("Login riuscito!")
            return redirect(url_for("app_routes.index"))

        flash("Credenziali non valide!")
    return render_template("login.html")


@app_routes.route("/confirm_email/<token>")
def confirm_email(token):
    try:
        email = get_email_token_serializer().loads(token, salt="email-confirm", max_age=60 * 60 * 24)
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
@limiter.limit(
    lambda: _rate_limit_value(
        Config.FORGOT_PASSWORD_RATE_LIMIT_ATTEMPTS,
        Config.FORGOT_PASSWORD_RATE_LIMIT_WINDOW_SECONDS,
    ),
    methods=["POST"],
    key_func=_client_ip_address,
)
def forgot_password():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        user = User.query.filter_by(email=email).first()
        if user:
            token_payload = {"email": user.email, "password_hash": user.password_hash}
            token = get_reset_token_serializer().dumps(token_payload, salt="password-reset")
            reset_url = url_for("app_routes.reset_password", token=token, _external=True)
            send_password_reset_email(user.email, reset_url)

        flash("Se l'email è registrata, riceverai un link per reimpostare la password.")
        return redirect(url_for("app_routes.login"))

    return render_template("forgot_password.html")


@app_routes.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        token_data = get_reset_token_serializer().loads(token, salt="password-reset", max_age=3600)
    except (SignatureExpired, BadSignature):
        flash("Link di recupero non valido o scaduto.")
        return redirect(url_for("app_routes.forgot_password"))

    if isinstance(token_data, str):
        email = token_data
        password_hash = None
    else:
        email = token_data.get("email")
        password_hash = token_data.get("password_hash")

    if not email:
        flash("Link di recupero non valido o scaduto.")
        return redirect(url_for("app_routes.forgot_password"))

    user = User.query.filter_by(email=email).first()
    if not user:
        flash("Utente non trovato.")
        return redirect(url_for("app_routes.forgot_password"))

    if password_hash is not None and password_hash != user.password_hash:
        flash("Link di recupero non valido o già utilizzato.")
        return redirect(url_for("app_routes.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        if password != confirm_password:
            flash("Le password non coincidono.")
            return redirect(url_for("app_routes.reset_password", token=token))

        if not _validate_password_requirements(password):
            flash(_password_requirements_message())
            return redirect(url_for("app_routes.reset_password", token=token))

        user.set_password(password)
        user.session_version += 1
        db.session.commit()
        flash("Password aggiornata con successo! Ora puoi accedere.")
        return redirect(url_for("app_routes.login"))

    return render_template("reset_password.html")


@app_routes.route("/logout")
def logout():
    user_id = session.get("user_id")
    if user_id:
        user = db.session.get(User, user_id)
        if user:
            user.session_version += 1
            db.session.commit()

    session.pop("user_id", None)
    session.pop("is_admin", None)
    session.pop("is_content_manager", None)
    session.pop(SESSION_LAST_ACTIVITY_KEY, None)
    session.pop(SESSION_VERSION_KEY, None)
    flash("Logout effettuato!")
    return redirect(url_for("app_routes.index"))
