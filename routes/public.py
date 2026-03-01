from flask import render_template
from sqlalchemy.sql.expression import func

from models import Book

from . import app_routes
from .core import _get_random_origin_quote


@app_routes.route("/")
def index():
    books = Book.query.filter_by(is_active=True).order_by(func.random()).limit(9).all()
    random_quote = _get_random_origin_quote()
    return render_template("index.html", books=books, random_quote=random_quote)
