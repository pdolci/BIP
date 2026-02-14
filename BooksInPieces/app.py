from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from extensions import db, mail, migrate
from routes import app_routes


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    mail.init_app(app)
    migrate.init_app(app, db)

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

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=app.config.get("DEBUG", False), use_reloader=False)
