from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail
from flask_migrate import Migrate
from flask_limiter import Limiter
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
mail = Mail()
migrate = Migrate()
limiter = Limiter()
csrf = CSRFProtect()
