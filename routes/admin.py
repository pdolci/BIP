from flask import flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy.orm import joinedload

from bip_logging import LEVELS, get_log_level_name, set_log_level
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
    require_admin,
)

ADMIN_PAGE_SIZE = 20


@app_routes.route("/admin/maintenance")
@require_admin
def admin_maintenance():
    page = request.args.get("page", 1, type=int)
    users_page = (
        User.query
        .options(joinedload(User.schedules).joinedload(ReadingSchedule.book))
        .order_by(User.created_at.desc())
        .paginate(page=page, per_page=ADMIN_PAGE_SIZE, error_out=False)
    )
    users = users_page.items
    all_schedules = [schedule for user in users for schedule in user.schedules]
    now = utc_now_naive()
    due_schedules = [s for s in all_schedules if not s.is_paused and s.next_send_date and s.next_send_date <= now]
    paused_schedules = [s for s in all_schedules if s.is_paused]

    total_users = User.query.count()
    total_schedules = ReadingSchedule.query.count()
    total_active = ReadingSchedule.query.filter_by(is_paused=False).count()
    total_paused = total_schedules - total_active

    return render_template(
        "admin_maintenance.html",
        users=users,
        users_page=users_page,
        now=now,
        total_users=total_users,
        total_schedules=total_schedules,
        active_schedules=total_active,
        paused_schedules=total_paused,
        due_schedules=len(due_schedules),
        describe_frequency=describe_frequency,
        describe_delivery_channel=describe_delivery_channel,
        format_app_datetime=format_app_datetime,
        log_level=get_log_level_name(),
        log_levels=list(LEVELS.keys()),
    )


@app_routes.route("/admin/log-level", methods=["GET"])
@require_admin
def admin_get_log_level():
    return jsonify({"level": get_log_level_name()})


@app_routes.route("/admin/log-level", methods=["POST"])
@require_admin
def admin_set_log_level():
    level = (request.form.get("level") or "").strip().lower()
    if not level:
        data = request.get_json(silent=True) or {}
        level = (data.get("level") or "").strip().lower()
    try:
        set_log_level(level)
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("app_routes.admin_maintenance"))
    flash(f"Livello di log impostato a «{level}».")
    return redirect(url_for("app_routes.admin_maintenance"))


@app_routes.route("/admin/schedule/toggle-pause/<int:schedule_id>", methods=["POST"])
@require_admin
def admin_toggle_pause_schedule(schedule_id):
    schedule = db.session.get(ReadingSchedule, schedule_id)
    if not schedule:
        flash("Schedulazione non trovata.")
        return redirect(url_for("app_routes.admin_maintenance"))

    schedule.is_paused = not schedule.is_paused
    db.session.commit()
    flash("Schedulazione messa in pausa." if schedule.is_paused else "Schedulazione riattivata.")
    return redirect(url_for("app_routes.admin_maintenance"))


@app_routes.route("/admin/schedule/recompute/<int:schedule_id>", methods=["POST"])
@require_admin
def admin_recompute_schedule(schedule_id):
    schedule = db.session.get(ReadingSchedule, schedule_id)
    if not schedule:
        flash("Schedulazione non trovata.")
        return redirect(url_for("app_routes.admin_maintenance"))

    next_local_date = compute_next_send_datetime(local_now_naive(), schedule, allow_immediate=False)
    schedule.next_send_date = local_naive_to_utc_naive(next_local_date)
    db.session.commit()
    flash("Prossimo invio ricalcolato con successo.")
    return redirect(url_for("app_routes.admin_maintenance"))


@app_routes.route("/admin/schedule/delete/<int:schedule_id>", methods=["POST"])
@require_admin
def admin_delete_schedule(schedule_id):
    schedule = db.session.get(ReadingSchedule, schedule_id)
    if not schedule:
        flash("Schedulazione non trovata.")
        return redirect(url_for("app_routes.admin_maintenance"))

    DeliveryEvent.query.filter_by(schedule_id=schedule.id).delete(synchronize_session=False)
    db.session.delete(schedule)
    db.session.commit()
    flash("Pianificazione eliminata.")
    return redirect(url_for("app_routes.admin_maintenance"))


@app_routes.route("/admin/schedule/reset-progress/<int:schedule_id>", methods=["POST"])
@require_admin
def admin_reset_schedule_progress(schedule_id):
    schedule = db.session.get(ReadingSchedule, schedule_id)
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
@require_admin
def admin_reset_user_password(user_id):
    user = db.session.get(User, user_id)
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
    user.session_version += 1
    db.session.commit()
    flash(f"Password aggiornata per {user.email}.")
    return redirect(url_for("app_routes.admin_maintenance"))


@app_routes.route("/admin/user/edit/<int:user_id>", methods=["POST"])
@require_admin
def admin_edit_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("Utente non trovato.")
        return redirect(url_for("app_routes.admin_maintenance"))

    new_email = (request.form.get("email") or "").strip().lower()
    telegram_handle = (request.form.get("telegram_handle") or "").strip()
    is_admin = request.form.get("is_admin") == "1"
    is_content_manager = request.form.get("is_content_manager") == "1"

    if not new_email or "@" not in new_email:
        flash("Email non valida.")
        return redirect(url_for("app_routes.admin_maintenance"))

    existing = User.query.filter(User.email == new_email, User.id != user_id).first()
    if existing:
        flash("Email già in uso da un altro utente.")
        return redirect(url_for("app_routes.admin_maintenance"))

    # Prevent removing admin role from self
    if session.get("user_id") == user_id and not is_admin:
        flash("Non puoi rimuovere il ruolo admin dal tuo stesso account.")
        return redirect(url_for("app_routes.admin_maintenance"))

    user.email = new_email
    user.telegram_handle = telegram_handle or None
    user.is_admin = is_admin
    user.is_content_manager = is_content_manager
    db.session.commit()
    flash(f"Anagrafica aggiornata per {user.email}.")
    return redirect(url_for("app_routes.admin_maintenance"))


@app_routes.route("/admin/user/delete/<int:user_id>", methods=["POST"])
@require_admin
def admin_delete_user(user_id):
    if session.get("user_id") == user_id:
        flash("Non puoi cancellare il tuo account amministratore da questa schermata.")
        return redirect(url_for("app_routes.admin_maintenance"))

    user = db.session.get(User, user_id)
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
