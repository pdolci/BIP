import os


def _get_required_env(var_name: str) -> str:
    value = os.environ.get(var_name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable '{var_name}'. "
            "Set it before starting the application."
        )
    return value


class Config:
    SECRET_KEY = _get_required_env("SECRET_KEY")

    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "books")

    SQLALCHEMY_DATABASE_URI = _get_required_env("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = _get_required_env("MAIL_USERNAME")
    MAIL_PASSWORD = _get_required_env("MAIL_PASSWORD")

    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Europe/Rome")
