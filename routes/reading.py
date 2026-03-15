from datetime import date, datetime, time, timedelta

from flask import flash, redirect, render_template, request, session, url_for
from sqlalchemy.orm import joinedload

from email_sender import DELIVERY_EMAIL, DELIVERY_TELEGRAM, SUPPORTED_DELIVERY_CHANNELS
from extensions import db
from models import Book, DeliveryEvent, ReadingSchedule, User
from schedule_utils import (
    FREQ_EVERY_N_DAYS,
    FREQ_WEEKDAYS,
    FREQ_WEEKEND,
    compute_next_send_datetime_from_params,
    parse_time_str,
    serialize_weekdays,
)
from semantic_search import semantic_book_index
from time_utils import compute_next_send_utc, local_naive_to_utc_naive, local_now_naive, utc_now_naive

from . import app_routes
from .core import (
    MAX_ACTIVE_SUBSCRIPTIONS,
    _build_book_filters,
    _build_dashboard,
    _count_book_words,
    _is_schedule_completed,
    _normalize_tags,
    _parse_int_form_field,
    describe_delivery_channel,
    describe_frequency,
    format_app_datetime,
    require_login,
)


MAX_TRAVEL_DAYS = 365


@app_routes.route("/select_book")
@require_login
def select_book():
    user_id = session["user_id"]
    active_schedules = ReadingSchedule.query.options(joinedload(ReadingSchedule.book)).filter_by(user_id=user_id).all()

    search_query = request.args.get("q", "").strip()
    filter_author = request.args.get("author", "").strip()
    filter_year = request.args.get("year", "").strip()
    filter_genre = request.args.get("genre", "").strip()
    filter_tags = request.args.get("tags", "").strip()
    filter_language = request.args.get("language", "").strip()
    filter_max_hours = request.args.get("max_hours", "").strip()
    selected_book_id = request.args.get("selected_book_id", type=int)

    has_active_filters = any([
        search_query,
        filter_author,
        filter_year,
        filter_genre,
        filter_tags,
        filter_language,
        filter_max_hours,
        selected_book_id,
    ])

    books = []
    if selected_book_id:
        selected_book = Book.query.filter_by(id=selected_book_id, is_active=True).first()
        books = [selected_book] if selected_book else []
    elif has_active_filters:
        filters = _build_book_filters(
            search_query,
            filter_author,
            filter_year,
            filter_genre,
            filter_tags,
            filter_language,
            filter_max_hours,
            semantic_book_ids=semantic_book_index.search(search_query) if search_query else None,
        )
        books_query = Book.query
        if filters:
            books_query = books_query.filter(*filters)
        books = books_query.order_by(Book.title.asc()).all()

    dashboard = _build_dashboard(active_schedules)

    return render_template(
        "select_book.html",
        books=books,
        active_schedules=active_schedules,
        dashboard=dashboard,
        describe_frequency=describe_frequency,
        describe_delivery_channel=describe_delivery_channel,
        search_query=search_query,
        filter_author=filter_author,
        filter_year=filter_year,
        filter_genre=filter_genre,
        filter_tags=filter_tags,
        filter_language=filter_language,
        filter_max_hours=filter_max_hours,
        has_active_filters=has_active_filters,
        normalize_tags=_normalize_tags,
    )


@app_routes.route("/configure_reading/<int:book_id>", methods=["GET", "POST"])
@require_login
def configure_reading(book_id):
    user_id = session["user_id"]
    selected_book = db.session.get(Book, book_id)
    if not selected_book:
        flash("Libro non trovato.")
        return redirect(url_for("app_routes.select_book"))

    active_schedules = ReadingSchedule.query.options(joinedload(ReadingSchedule.book)).filter_by(user_id=user_id).all()
    current_user = db.session.get(User, user_id)

    if request.method == "POST":
        try:
            minutes_per_reading = _parse_int_form_field("minutes_per_reading", min_value=1, max_value=30, label="minuti per lettura")
            words_per_minute = _parse_int_form_field("words_per_minute", default=200, min_value=1, label="parole al minuto")
            frequency_days = _parse_int_form_field("frequency_days", default=1, min_value=1, label="frequenza in giorni")
        except ValueError as error:
            flash(str(error))
            return redirect(url_for("app_routes.configure_reading", book_id=book_id))

        frequency_type = request.form.get("frequency_type") or request.form.get("frequency_mode", FREQ_EVERY_N_DAYS)
        if frequency_type == "interval":
            frequency_type = FREQ_EVERY_N_DAYS

        raw_delivery_time = (request.form.get("delivery_time") or "").strip()
        delivery_time = parse_time_str(raw_delivery_time)
        if raw_delivery_time and delivery_time is None:
            flash("Orario di consegna non valido. Usa un formato come 07:30 o un valore come 'dopo cena'.")
            return redirect(url_for("app_routes.configure_reading", book_id=book_id))
        weekdays_selected = request.form.getlist("weekdays") or request.form.getlist("frequency_weekdays")
        default_channel = (current_user.preferred_delivery_channel if current_user else DELIVERY_EMAIL)
        delivery_channel = (request.form.get("delivery_channel") or default_channel).strip().lower()

        if delivery_channel not in SUPPORTED_DELIVERY_CHANNELS:
            flash("Canale di consegna non valido.")
            return redirect(url_for("app_routes.configure_reading", book_id=book_id))

        if delivery_channel == DELIVERY_TELEGRAM and not (current_user and current_user.telegram_handle):
            flash("Aggiungi il tuo handle Telegram nel profilo per usare questo canale.")
            return redirect(url_for("app_routes.configure_reading", book_id=book_id))

        active_subscriptions = sum(1 for schedule in active_schedules if not schedule.is_paused)
        if active_subscriptions >= MAX_ACTIVE_SUBSCRIPTIONS:
            flash("Hai raggiunto il limite di 3 sottoscrizioni attive. Metti in pausa o cancella una sottoscrizione prima di aggiungerne un'altra.")
            return redirect(url_for("app_routes.configure_reading", book_id=book_id))

        if frequency_type == FREQ_WEEKDAYS and not weekdays_selected:
            flash("Seleziona almeno un giorno della settimana.")
            return redirect(url_for("app_routes.configure_reading", book_id=book_id))

        if frequency_type != FREQ_EVERY_N_DAYS:
            frequency_days = 1

        weekdays = None
        if frequency_type == FREQ_WEEKDAYS:
            weekdays = serialize_weekdays([int(day) for day in weekdays_selected if day.isdigit()])
        elif frequency_type == FREQ_WEEKEND:
            weekdays = serialize_weekdays([5, 6])

        next_send_date = compute_next_send_datetime_from_params(
            local_now_naive(),
            frequency_type,
            frequency_days,
            weekdays,
            delivery_time,
            allow_immediate=True,
        )

        schedule = ReadingSchedule(
            user_id=user_id,
            book_id=selected_book.id,
            words_per_minute=words_per_minute,
            minutes_per_reading=minutes_per_reading,
            frequency_type=frequency_type,
            frequency_days=frequency_days,
            weekdays=weekdays,
            delivery_time=delivery_time,
            next_send_date=local_naive_to_utc_naive(next_send_date),
            is_paused=False,
            delivery_channel=delivery_channel,
        )
        db.session.add(schedule)
        db.session.commit()
        flash("Programma di lettura impostato!")
        return redirect(url_for("app_routes.reading_center"))

    return render_template(
        "configure_reading.html",
        selected_book=selected_book,
        delivery_email=DELIVERY_EMAIL,
        delivery_telegram=DELIVERY_TELEGRAM,
        session_telegram_handle=(current_user.telegram_handle if current_user else ""),
        default_delivery_channel=(current_user.preferred_delivery_channel if current_user else DELIVERY_EMAIL),
    )


@app_routes.route("/reading_center")
@require_login
def reading_center():
    active_schedules = ReadingSchedule.query.options(joinedload(ReadingSchedule.book)).filter_by(user_id=session["user_id"]).all()
    dashboard = _build_dashboard(active_schedules)

    schedule_infos = {}
    for schedule in active_schedules:
        total = schedule.book.word_count or 0
        read = min(schedule.last_sent_index, total) if total else schedule.last_sent_index
        pct = int(round(read / total * 100)) if total else 0
        words_next = schedule.words_per_minute * schedule.minutes_per_reading
        schedule_infos[schedule.id] = {
            "completion_pct": pct,
            "words_next": words_next,
        }

    return render_template(
        "reading_center.html",
        active_schedules=active_schedules,
        dashboard=dashboard,
        describe_frequency=describe_frequency,
        describe_delivery_channel=describe_delivery_channel,
        format_app_datetime=format_app_datetime,
        schedule_infos=schedule_infos,
    )


@app_routes.route("/snooze_schedule/<int:schedule_id>", methods=["POST"])
@require_login
def snooze_schedule(schedule_id):
    schedule = db.session.get(ReadingSchedule, schedule_id)
    if not schedule or schedule.user_id != session["user_id"]:
        flash("Operazione non consentita.")
        return redirect(url_for("app_routes.select_book"))

    action = request.form.get("action")
    now = utc_now_naive()

    if action == "skip_next":
        schedule.skip_next = True
        schedule.snooze_until = None
        flash("La prossima consegna sarà saltata.")
    elif action == "delay_24h":
        base_time = schedule.next_send_date if schedule.next_send_date and schedule.next_send_date > now else now
        schedule.snooze_until = base_time + timedelta(hours=24)
        schedule.next_send_date = schedule.snooze_until
        schedule.skip_next = False
        flash("Consegna rimandata di 24 ore.")
    else:
        flash("Azione di snooze non valida.")

    db.session.commit()
    return redirect(url_for("app_routes.select_book"))


@app_routes.route("/travel_mode/<int:schedule_id>", methods=["POST"])
@require_login
def travel_mode(schedule_id):
    schedule = db.session.get(ReadingSchedule, schedule_id)
    if not schedule or schedule.user_id != session["user_id"]:
        flash("Operazione non consentita.")
        return redirect(url_for("app_routes.select_book"))

    action = request.form.get("action")
    if action == "clear":
        schedule.travel_pause_until = None
        db.session.commit()
        flash("Modalità viaggio disattivata.")
        return redirect(url_for("app_routes.select_book"))

    resume_date_raw = request.form.get("resume_date")
    resume_time_raw = request.form.get("resume_time")
    if not resume_date_raw:
        flash("Seleziona una data di ripartenza.")
        return redirect(url_for("app_routes.select_book"))

    try:
        resume_date = date.fromisoformat(resume_date_raw)
    except ValueError:
        flash("Data di ripartenza non valida.")
        return redirect(url_for("app_routes.select_book"))

    parsed_resume_time = parse_time_str(resume_time_raw) if resume_time_raw else None
    if resume_time_raw and parsed_resume_time is None:
        flash("Orario di ripartenza non valido. Usa un formato come 07:30.")
        return redirect(url_for("app_routes.select_book"))

    resume_time = parsed_resume_time or schedule.delivery_time or time(9, 0)
    resume_at = local_naive_to_utc_naive(datetime.combine(resume_date, resume_time))

    if resume_at <= utc_now_naive():
        flash("La ripartenza deve essere nel futuro.")
        return redirect(url_for("app_routes.select_book"))

    schedule.travel_pause_until = resume_at
    if schedule.next_send_date is None or schedule.next_send_date < resume_at:
        schedule.next_send_date = resume_at

    db.session.commit()
    flash("Modalità viaggio attivata.")
    return redirect(url_for("app_routes.select_book"))


@app_routes.route("/pause_schedule/<int:schedule_id>", methods=["POST"])
@require_login
def pause_schedule(schedule_id):
    schedule = db.session.get(ReadingSchedule, schedule_id)
    if schedule and schedule.user_id == session["user_id"]:
        schedule.is_paused = not schedule.is_paused
        db.session.add(DeliveryEvent(
            schedule_id=schedule.id,
            event_type="paused" if schedule.is_paused else "resumed",
            words_count=0,
            note="Pausa attivata" if schedule.is_paused else "Lettura ripresa",
        ))
        db.session.commit()
        flash("Sottoscrizione aggiornata con successo!")
    else:
        flash("Operazione non consentita.")

    return redirect(url_for("app_routes.select_book"))


@app_routes.route("/snooze_next_schedule/<int:schedule_id>", methods=["POST"])
@require_login
def snooze_next_schedule(schedule_id):
    schedule = db.session.get(ReadingSchedule, schedule_id)
    if not schedule or schedule.user_id != session["user_id"]:
        flash("Operazione non consentita.")
        return redirect(url_for("app_routes.select_book"))

    total_words = _count_book_words(schedule)
    if _is_schedule_completed(schedule, total_words):
        flash("Libro già completato: non puoi rimandare ulteriormente questa consegna.")
        return redirect(url_for("app_routes.select_book"))

    reference = max(schedule.next_send_date, utc_now_naive())
    schedule.next_send_date = compute_next_send_utc(schedule, reference, allow_immediate=False)
    db.session.add(DeliveryEvent(schedule_id=schedule.id, event_type="skipped", words_count=0, note="Salto prossima consegna"))
    db.session.commit()
    flash("Prossima consegna saltata con successo.")
    return redirect(url_for("app_routes.select_book"))


@app_routes.route("/snooze_24h_schedule/<int:schedule_id>", methods=["POST"])
@require_login
def snooze_24h_schedule(schedule_id):
    schedule = db.session.get(ReadingSchedule, schedule_id)
    if not schedule or schedule.user_id != session["user_id"]:
        flash("Operazione non consentita.")
        return redirect(url_for("app_routes.select_book"))

    total_words = _count_book_words(schedule)
    if _is_schedule_completed(schedule, total_words):
        flash("Libro già completato: non puoi rimandare ulteriormente questa consegna.")
        return redirect(url_for("app_routes.select_book"))

    schedule.next_send_date = max(schedule.next_send_date, utc_now_naive()) + timedelta(hours=24)
    db.session.add(DeliveryEvent(schedule_id=schedule.id, event_type="skipped", words_count=0, note="Rimandata di 24 ore"))
    db.session.commit()
    flash("Consegna rimandata di 24 ore.")
    return redirect(url_for("app_routes.select_book"))


@app_routes.route("/travel_mode_schedule/<int:schedule_id>", methods=["POST"])
@require_login
def travel_mode_schedule(schedule_id):
    schedule = db.session.get(ReadingSchedule, schedule_id)
    if not schedule or schedule.user_id != session["user_id"]:
        flash("Operazione non consentita.")
        return redirect(url_for("app_routes.select_book"))

    try:
        travel_days = int(request.form.get("travel_days", 0) or 0)
    except (TypeError, ValueError):
        flash("Inserisci un numero di giorni valido per la modalità viaggio.")
        return redirect(url_for("app_routes.select_book"))

    if travel_days <= 0 or travel_days > MAX_TRAVEL_DAYS:
        flash(f"I giorni di modalità viaggio devono essere compresi tra 1 e {MAX_TRAVEL_DAYS}.")
        return redirect(url_for("app_routes.select_book"))

    schedule.travel_pause_until = utc_now_naive() + timedelta(days=travel_days)
    if schedule.next_send_date < schedule.travel_pause_until:
        schedule.next_send_date = schedule.travel_pause_until
    db.session.add(DeliveryEvent(
        schedule_id=schedule.id,
        event_type="skipped",
        words_count=0,
        note=f"Modalità viaggio ({travel_days} giorni)",
    ))
    db.session.commit()
    flash(f"Modalità viaggio attivata per {travel_days} giorni.")
    return redirect(url_for("app_routes.select_book"))


@app_routes.route("/delete_schedule/<int:schedule_id>", methods=["POST"])
@require_login
def delete_schedule(schedule_id):
    schedule = db.session.get(ReadingSchedule, schedule_id)
    if schedule and schedule.user_id == session["user_id"]:
        DeliveryEvent.query.filter_by(schedule_id=schedule.id).delete(synchronize_session=False)
        db.session.delete(schedule)
        db.session.commit()
        flash("Sottoscrizione eliminata con successo!")
    else:
        flash("Operazione non consentita.")

    return redirect(url_for("app_routes.select_book"))
