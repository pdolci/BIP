import os
import uuid

from flask import abort, flash, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from bip_logging import get_logger
from email_sender import count_total_words, send_next_book_part
from extensions import db
from models import Book
from semantic_search import build_and_store_book_embedding, semantic_book_index

logger = get_logger(__name__)

from . import app_routes
from .core import (
    COVER_UPLOAD_FOLDER,
    UPLOAD_FOLDER,
    _delete_local_cover_if_present,
    _sanitize_uploaded_book_file,
    _store_cover_on_disk,
    allowed_file,
    require_book_management,
)


@app_routes.route("/manage_books")
@require_book_management
def manage_books():
    books = Book.query.order_by(Book.title.asc()).all()
    return render_template("admin_books.html", books=books)


@app_routes.route("/upload_book", methods=["POST"])
@require_book_management
def upload_book():
    title = (request.form.get("title") or "").strip()
    file = request.files.get("book_file")
    if not title or not file or not file.filename:
        flash("Inserisci obbligatoriamente il nome del libro e il file da caricare.")
        return redirect(url_for("app_routes.manage_books"))

    short_description = request.form.get("short_description")
    author = request.form.get("author")
    publication_year = request.form.get("publication_year")
    genre = request.form.get("genre")
    tags = request.form.get("tags")
    language = request.form.get("language")
    estimated_reading_hours = request.form.get("estimated_reading_hours")
    cover_image_file = request.files.get("cover_image_file")

    if file and allowed_file(file.filename):
        original_filename = secure_filename(file.filename)
        extension = original_filename.rsplit(".", 1)[1].lower() if "." in original_filename else ""
        filename = f"{uuid.uuid4().hex}.{extension}" if extension else uuid.uuid4().hex
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        try:
            file.save(file_path)
            _sanitize_uploaded_book_file(file_path, extension)
            if not os.path.isfile(file_path):
                logger.error("❌ File non trovato dopo il salvataggio: %s", file_path)
                flash("Errore nel caricamento: inserisci il nome del libro e un file valido.")
                return redirect(url_for("app_routes.manage_books"))
        except Exception as error:
            logger.error("❌ Errore durante il salvataggio/sanitizzazione del file: %s", error)
            flash("Errore nel caricamento: inserisci il nome del libro e un file valido.")
            return redirect(url_for("app_routes.manage_books"))

        parsed_publication_year = int(publication_year) if publication_year and publication_year.isdigit() else None
        try:
            parsed_estimated_hours = float(estimated_reading_hours) if estimated_reading_hours else None
        except ValueError:
            parsed_estimated_hours = None

        cover_filename = _store_cover_on_disk(cover_image_file) if cover_image_file and cover_image_file.filename else None
        if cover_image_file and cover_image_file.filename and not cover_filename:
            flash("Formato copertina non supportato. Usa PNG, JPG, GIF o WEBP.")
            return redirect(url_for("app_routes.manage_books"))

        new_book = Book(
            title=title,
            file_path=filename,
            is_active=True,
            short_description=short_description,
            author=author,
            publication_year=parsed_publication_year,
            genre=genre,
            tags=tags,
            language=language,
            estimated_reading_hours=parsed_estimated_hours,
            cover_image=f"covers/{cover_filename}" if cover_filename else None,
            word_count=count_total_words(file_path),
        )
        build_and_store_book_embedding(new_book)
        db.session.add(new_book)
        db.session.commit()
        semantic_book_index.upsert(new_book)
        flash("Libro caricato con successo!")
    else:
        flash("Formato non supportato. Carica file .txt, .html o .htm.")

    return redirect(url_for("app_routes.manage_books"))


@app_routes.route("/admin/book/edit/<int:book_id>", methods=["POST"])
@require_book_management
def edit_book(book_id):
    book = db.session.get(Book, book_id)
    if not book:
        flash("Libro non trovato.")
        return redirect(url_for("app_routes.manage_books"))

    title = (request.form.get("title") or "").strip()
    if not title:
        flash("Il titolo è obbligatorio.")
        return redirect(url_for("app_routes.manage_books"))

    publication_year = (request.form.get("publication_year") or "").strip()
    estimated_reading_hours = (request.form.get("estimated_reading_hours") or "").strip()
    parsed_publication_year = int(publication_year) if publication_year.isdigit() else None
    try:
        parsed_estimated_hours = float(estimated_reading_hours) if estimated_reading_hours else None
    except ValueError:
        parsed_estimated_hours = None

    book.title = title
    book.short_description = (request.form.get("short_description") or "").strip() or None
    book.author = (request.form.get("author") or "").strip() or None
    book.publication_year = parsed_publication_year
    book.genre = (request.form.get("genre") or "").strip() or None
    book.tags = (request.form.get("tags") or "").strip() or None
    book.language = (request.form.get("language") or "").strip() or None
    book.estimated_reading_hours = parsed_estimated_hours

    cover_image_file = request.files.get("cover_image_file")
    cover_filename = _store_cover_on_disk(cover_image_file) if cover_image_file and cover_image_file.filename else None
    if cover_image_file and cover_image_file.filename and not cover_filename:
        flash("Formato copertina non supportato. Usa PNG, JPG, GIF o WEBP.")
        return redirect(url_for("app_routes.manage_books"))

    if cover_filename:
        _delete_local_cover_if_present(book.cover_image)
        book.cover_image = f"covers/{cover_filename}"

    file_path = book.get_absolute_path()
    if os.path.exists(file_path):
        book.word_count = count_total_words(file_path)

    build_and_store_book_embedding(book)
    db.session.commit()
    semantic_book_index.upsert(book)
    flash("Libro aggiornato con successo!")
    return redirect(url_for("app_routes.manage_books"))


@app_routes.route("/uploads/books/<filename>")
@require_book_management
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)


@app_routes.route("/uploads/covers/<filename>")
def uploaded_cover(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in {"png", "jpg", "jpeg", "gif", "webp"}:
        abort(404)
    return send_from_directory(COVER_UPLOAD_FOLDER, filename)


@app_routes.route("/book_cover/<int:book_id>")
def book_cover(book_id):
    book = db.session.get(Book, book_id)
    if not book:
        abort(404)
    if not book.cover_image:
        return redirect(url_for("static", filename="images/cover.png"))

    if book.cover_image.startswith("covers/"):
        return redirect(url_for("app_routes.uploaded_cover", filename=book.cover_image.split("/", 1)[1]))

    abort(404)


@app_routes.route("/admin/book/delete/<int:book_id>", methods=["POST"])
@require_book_management
def delete_book(book_id):
    book = db.session.get(Book, book_id)
    if book:
        try:
            semantic_book_index.remove(book.id)
            db.session.delete(book)
            db.session.commit()
            flash("Libro eliminato!")
        except Exception as error:
            flash(f"Errore durante l'eliminazione del file: {error}")

    return redirect(url_for("app_routes.manage_books"))


@app_routes.route("/admin/book/toggle/<int:book_id>", methods=["POST"])
@require_book_management
def toggle_book_status(book_id):
    book = db.session.get(Book, book_id)
    if book:
        book.is_active = not book.is_active
        db.session.commit()
        flash("Stato del libro aggiornato!")

    return redirect(url_for("app_routes.manage_books"))


@app_routes.route("/test_email_sending/<int:schedule_id>")
@require_book_management
def test_email_sending(schedule_id):
    send_next_book_part(schedule_id)
    flash("Email inviata con successo!")
    return redirect(url_for("app_routes.manage_books"))
