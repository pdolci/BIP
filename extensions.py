from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail
from flask_migrate import Migrate
from flask_limiter import Limiter

db = SQLAlchemy()
mail = Mail()
migrate = Migrate()
limiter = Limiter()
