from flask import Blueprint

app_routes = Blueprint("app_routes", __name__)

# Import route modules for side effects (registration on blueprint)
from . import session_hooks  # noqa: E402,F401
from . import public  # noqa: E402,F401
from . import auth  # noqa: E402,F401
from . import user_profile  # noqa: E402,F401
from . import reading  # noqa: E402,F401
from . import admin  # noqa: E402,F401
from . import books  # noqa: E402,F401
