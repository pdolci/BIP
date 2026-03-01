from flask import flash, redirect, render_template, request, session, url_for
from sqlalchemy.orm import joinedload

from extensions import db
from models import DeliveryEvent, ReadingSchedule, User
from schedule_utils import compute_next_send_datetime
from time_utils import local_naive_to_utc_naive, local_now_naive, utc_now_naive

from . import app_routes
from .core import (
    _password_requirements_message,
    _validate_password_requirements,
    describe_delivery_channel,
    describe_frequency,
    format_app_datetime,
    has_admin_access,
    is_logged_in,
)


@app_routes.route("/admin/maintenance")
def admin_maintenance():
    if not is_logged_in() or not has_admin_access():
        flash("Accesso negato!")
        return redirect(url_for("app_routes.index"))

    users = User.query.options(joinedload(User.schedules).joinedload(ReadingSchedule.book)).order_by(User.created_at.desc()).all()
    all_schedules = [schedule for user in users for schedule in user.schedules]
    now = utc_now_naive()
    due_schedules = [s for s in all_schedules if not s.is_paused and s.next_send_date and s.next_send_date <= now]
    paused_schedules = [s for s in all_schedules if s.is_paused]

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

    next_local_date = compute_next_send_datetime(local_now_naive(), schedule, allow_immediate=False)
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
    db.session.add(DeliveryEvent(schedule_id=schedule.id, event_type="skipped", words_count=0, note="Reset progresso eseguito da admin"))
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
