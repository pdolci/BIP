import argparse
import os

# During setup we intentionally bootstrap missing prerequisites,
# so runtime guardrails must be temporarily disabled.
os.environ.setdefault("STARTUP_GUARDRAILS_ENABLED", "false")

from flask_migrate import upgrade

from app import create_app
from config import Config
from extensions import db


def ensure_directories() -> None:
    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(Config.COVER_UPLOAD_FOLDER, exist_ok=True)


def initialize_database(apply_migrations: bool = True) -> None:
    app = create_app()
    with app.app_context():
        if apply_migrations:
            upgrade(directory="migrations")
        db.create_all()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Esegue il setup iniziale del server: crea cartelle necessarie "
            "e inizializza/aggiorna il database."
        )
    )
    parser.add_argument(
        "--skip-migrations",
        action="store_true",
        help="Salta l'esecuzione delle migrazioni Alembic.",
    )
    args = parser.parse_args()

    ensure_directories()
    initialize_database(apply_migrations=not args.skip_migrations)


if __name__ == "__main__":
    main()
