from flask import Flask
from config import Config
from extensions import db, mail, migrate
from routes import app_routes
from scheduler import start_scheduler
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
    start_scheduler(app)

atexit.register(lambda: scheduler.shutdown())

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
