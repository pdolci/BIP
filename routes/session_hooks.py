from flask import flash, redirect, request, session, url_for

from config import Config
from extensions import db
from time_utils import utc_now_timestamp

from . import app_routes
from models import User

from .core import SESSION_LAST_ACTIVITY_KEY, SESSION_VERSION_KEY


@app_routes.before_app_request
def enforce_session_inactivity_timeout():
    user_id = session.get("user_id")
    if not user_id:
        return None

    endpoint = request.endpoint or ""
    if endpoint in {"app_routes.login", "app_routes.logout", "static"}:
        return None

    user = db.session.get(User, user_id)
    if not user:
        session.clear()
        flash("Sessione non valida. Effettua nuovamente il login.")
        return redirect(url_for("app_routes.login"))

    session_version = session.get(SESSION_VERSION_KEY)
    if session_version != user.session_version:
        session.clear()
        flash("Sessione revocata. Effettua nuovamente il login.")
        return redirect(url_for("app_routes.login"))

    timeout_seconds = max(Config.SESSION_INACTIVITY_MINUTES, 1) * 60
    now_ts = utc_now_timestamp()
    last_activity_ts = session.get(SESSION_LAST_ACTIVITY_KEY)

    if isinstance(last_activity_ts, int) and now_ts - last_activity_ts > timeout_seconds:
        session.clear()
        flash("Sessione scaduta per inattività. Effettua nuovamente il login.")
        return redirect(url_for("app_routes.login"))

    session[SESSION_LAST_ACTIVITY_KEY] = now_ts
    return None
