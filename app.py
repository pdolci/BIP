import os

from flask import Flask, flash, redirect, request, url_for
from limits.storage import storage_from_string
from werkzeug.middleware.proxy_fix import ProxyFix

from bip_logging import get_logger, setup_logging
from config import Config

logger = get_logger(__name__)

from extensions import csrf, db, limiter, mail, migrate
from routes import app_routes
from semantic_search import initialize_semantic_search_index


def _register_rate_limit_handlers(app):
    @app.errorhandler(429)
    def handle_rate_limit(_error):
        endpoint = request.endpoint or ""

        if endpoint == "app_routes.login":
            flash("Troppi tentativi di login. Riprova tra qualche minuto.")
            return redirect(url_for("app_routes.login"))

        if endpoint == "app_routes.forgot_password":
            flash("Troppi tentativi di recupero password. Riprova tra qualche minuto.")
            return redirect(url_for("app_routes.forgot_password"))

        if endpoint == "app_routes.register":
            flash("Troppi tentativi di registrazione. Riprova tra qualche minuto.")
            return redirect(url_for("app_routes.register"))

        return "Too many requests", 429

    @app.errorhandler(413)
    def handle_request_entity_too_large(_error):
        flash("Il file caricato supera il limite massimo consentito.")
        return redirect(request.referrer or url_for("app_routes.index"))

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        return response


def _validate_startup_prerequisites(app):
    if not app.config.get("STARTUP_GUARDRAILS_ENABLED", True):
        return

    required_directories = [
        app.config["UPLOAD_FOLDER"],
        app.config["COVER_UPLOAD_FOLDER"],
    ]

    missing_directories = [path for path in required_directories if not os.path.isdir(path)]
    if not missing_directories:
        return

    missing_list = ", ".join(missing_directories)
    raise RuntimeError(
        "Bootstrap incompleto: directory mancanti "
        f"({missing_list}). Esegui prima 'python server_setup.py'."
    )


def _configure_rate_limiter_storage(app):
    if not app.config.get("RATELIMIT_BACKEND_HEALTHCHECK_ENABLED", True):
        return

    storage_uri = app.config.get("RATELIMIT_STORAGE_URI")
    if not storage_uri:
        raise RuntimeError("RATE_LIMIT_STORAGE_URI non configurato.")

    try:
        storage = storage_from_string(storage_uri)
    except Exception as exc:  # pragma: no cover - external backend parsing
        message = f"Configurazione rate limit non valida ({storage_uri}): {exc}"
        if app.config.get("RATELIMIT_FAIL_ON_BACKEND_ERROR", True):
            raise RuntimeError(message) from exc

        fallback_uri = app.config.get("RATELIMIT_STORAGE_FALLBACK_URI", "memory://")
        logger.warning("%s. Fallback su %s.", message, fallback_uri)
        app.config["RATELIMIT_STORAGE_URI"] = fallback_uri
        return

    if storage.check():
        return

    message = (
        "Backend rate limit non raggiungibile "
        f"({storage_uri}). Il rate limit di login/forgot_password sarebbe inattivo."
    )
    if app.config.get("RATELIMIT_FAIL_ON_BACKEND_ERROR", True):
        raise RuntimeError(message)

    fallback_uri = app.config.get("RATELIMIT_STORAGE_FALLBACK_URI", "memory://")
    logger.warning("%s Fallback su %s.", message, fallback_uri)
    app.config["RATELIMIT_STORAGE_URI"] = fallback_uri


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    setup_logging(app)
    mail.init_app(app)
    migrate.init_app(app, db)
    _configure_rate_limiter_storage(app)
    limiter.init_app(app)
    csrf.init_app(app)

    app.register_blueprint(app_routes)

    if app.config.get("USE_PROXY_FIX"):
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=app.config.get("PROXY_FIX_X_FOR", 1),
            x_proto=app.config.get("PROXY_FIX_X_PROTO", 1),
            x_host=app.config.get("PROXY_FIX_X_HOST", 0),
            x_port=app.config.get("PROXY_FIX_X_PORT", 0),
            x_prefix=app.config.get("PROXY_FIX_X_PREFIX", 0),
        )

    _register_rate_limit_handlers(app)
    _validate_startup_prerequisites(app)

    with app.app_context():
        initialize_semantic_search_index(db.session)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=app.config.get("DEBUG", False), use_reloader=False)
