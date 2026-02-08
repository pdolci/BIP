from flask import Flask
from config import Config
from extensions import db, mail, migrate
from routes import app_routes
import scheduler as scheduler_module
import atexit

app = Flask(__name__)
app.config.from_object(Config)


db.init_app(app)
mail.init_app(app)
migrate.init_app(app, db)

# Registra le blueprints delle route
app.register_blueprint(app_routes)

# ✅ Passiamo `app` allo scheduler per evitare import circolari
with app.app_context():
    scheduler_module.start_scheduler(app)


def shutdown_scheduler():
    if scheduler_module.scheduler:
        scheduler_module.scheduler.shutdown()


atexit.register(shutdown_scheduler)

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
