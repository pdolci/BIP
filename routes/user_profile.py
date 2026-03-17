from flask import flash, redirect, render_template, request, session, url_for

from email_sender import DELIVERY_EMAIL, DELIVERY_TELEGRAM, SUPPORTED_DELIVERY_CHANNELS
from extensions import db
from models import DeliveryEvent, ReadingSchedule, User

from . import app_routes
from .core import _is_valid_email, _password_requirements_message, _validate_password_requirements, require_login


@app_routes.route("/profile", methods=["GET", "POST"])
@require_login
def profile():
    user = db.session.get(User, session["user_id"])
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
            user.session_version += 1

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
@require_login
def delete_account():
    confirmation = (request.form.get("delete_confirmation") or "").strip().upper()
    if confirmation != "ELIMINA":
        flash("Per cancellare l'account digita ELIMINA nel campo di conferma.")
        return redirect(url_for("app_routes.profile"))

    user = db.session.get(User, session["user_id"])
    if not user:
        flash("Utente non trovato.")
        return redirect(url_for("app_routes.logout"))

    schedules = ReadingSchedule.query.filter_by(user_id=user.id).all()
    for schedule in schedules:
        DeliveryEvent.query.filter_by(schedule_id=schedule.id).delete(synchronize_session=False)
        db.session.delete(schedule)

    db.session.delete(user)
    db.session.commit()

    session.clear()
    flash("Il tuo account è stato disiscritto e cancellato completamente.")
    return redirect(url_for("app_routes.index"))
