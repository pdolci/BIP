import os
from datetime import timedelta


def _load_dotenv_if_present() -> None:
    """Load environment variables from a local .env file if present."""
    base_dir = os.path.abspath(os.path.dirname(__file__))
    candidate_paths = [
        os.path.join(base_dir, ".env"),
        os.path.join(os.path.dirname(base_dir), ".env"),
    ]

    for env_path in candidate_paths:
        if not os.path.isfile(env_path):
            continue

        with open(env_path, "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue

                if line.startswith("export "):
                    line = line[len("export "):].strip()

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")

                if key:
                    os.environ.setdefault(key, value)
        break


_load_dotenv_if_present()


def _get_required_env(var_name: str) -> str:
    value = os.environ.get(var_name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable '{var_name}'. "
            "Set it before starting the application."
        )
    return value


def _get_bool_env(var_name: str, default: bool) -> bool:
    value = os.environ.get(var_name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _get_int_env(var_name: str, default: int) -> int:
    value = os.environ.get(var_name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


class Config:
    SECRET_KEY = _get_required_env("SECRET_KEY")

    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "books")
    COVER_UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "covers")

    SQLALCHEMY_DATABASE_URI = _get_required_env("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = _get_required_env("MAIL_USERNAME")
    MAIL_PASSWORD = _get_required_env("MAIL_PASSWORD")

    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Europe/Rome")

    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

    DEBUG = _get_bool_env("FLASK_DEBUG", False)
    SESSION_COOKIE_SECURE = _get_bool_env("SESSION_COOKIE_SECURE", True)
    SESSION_COOKIE_HTTPONLY = _get_bool_env("SESSION_COOKIE_HTTPONLY", True)
    SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
    SESSION_INACTIVITY_MINUTES = _get_int_env("SESSION_INACTIVITY_MINUTES", 10)
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=SESSION_INACTIVITY_MINUTES)
    PREFERRED_URL_SCHEME = os.environ.get("PREFERRED_URL_SCHEME", "https")

    LOGIN_RATE_LIMIT_ATTEMPTS = _get_int_env("LOGIN_RATE_LIMIT_ATTEMPTS", 5)
    LOGIN_RATE_LIMIT_WINDOW_SECONDS = _get_int_env("LOGIN_RATE_LIMIT_WINDOW_SECONDS", 300)
    FORGOT_PASSWORD_RATE_LIMIT_ATTEMPTS = _get_int_env("FORGOT_PASSWORD_RATE_LIMIT_ATTEMPTS", 3)
    FORGOT_PASSWORD_RATE_LIMIT_WINDOW_SECONDS = _get_int_env("FORGOT_PASSWORD_RATE_LIMIT_WINDOW_SECONDS", 600)

    USE_PROXY_FIX = _get_bool_env("USE_PROXY_FIX", True)
    PROXY_FIX_X_FOR = _get_int_env("PROXY_FIX_X_FOR", 1)
    PROXY_FIX_X_PROTO = _get_int_env("PROXY_FIX_X_PROTO", 1)
    PROXY_FIX_X_HOST = _get_int_env("PROXY_FIX_X_HOST", 0)
    PROXY_FIX_X_PORT = _get_int_env("PROXY_FIX_X_PORT", 0)
    PROXY_FIX_X_PREFIX = _get_int_env("PROXY_FIX_X_PREFIX", 0)
