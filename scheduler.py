import logging

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask

from email_sender import send_next_book_part
from extensions import db
from models import ReadingSchedule
from schedule_utils import compute_next_send_datetime
from time_utils import utc_now_naive

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

scheduler = None


def _defer_until_if_needed(schedule, now, pause_until):
    if not pause_until:
        return False

    if now < pause_until:
        if schedule.next_send_date and schedule.next_send_date < pause_until:
            schedule.next_send_date = pause_until
            db.session.commit()
        return True

    return False


def _release_pause_if_elapsed(schedule, now, field_name):
    pause_until = getattr(schedule, field_name)
    if pause_until and now >= pause_until:
        setattr(schedule, field_name, None)
        db.session.commit()


def _handle_skip_next(schedule, now):
    if not schedule.skip_next:
        return False
    schedule.skip_next = False
    schedule.next_send_date = compute_next_send_datetime(now, schedule, allow_immediate=False)
    db.session.commit()
    return True


def _due_schedules(now):
    return db.session.query(ReadingSchedule).filter(
        ReadingSchedule.next_send_date <= now,
        ReadingSchedule.is_paused.is_(False),
        db.or_(ReadingSchedule.travel_pause_until.is_(None), ReadingSchedule.travel_pause_until <= now),
    ).all()


def check_scheduled_emails(app: Flask):
    logging.info("✅ Job check_scheduled_emails AVVIATO")

    with app.app_context():
        now = utc_now_naive()
        db.session.expire_all()
        schedules = _due_schedules(now)

        if not schedules:
            logging.info("⚠️ Nessun programma di lettura trovato con next_send_date passato.")
            return

        for schedule in schedules:
            if _defer_until_if_needed(schedule, now, schedule.travel_pause_until):
                continue
            _release_pause_if_elapsed(schedule, now, "travel_pause_until")

            if _defer_until_if_needed(schedule, now, schedule.snooze_until):
                continue
            _release_pause_if_elapsed(schedule, now, "snooze_until")

            if _handle_skip_next(schedule, now):
                continue

            send_next_book_part(schedule.id)

    logging.info("✅ Job check_scheduled_emails COMPLETATO")


def start_scheduler(app: Flask):
    global scheduler
    if scheduler is None:
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            check_scheduled_emails,
            "interval",
            minutes=1,
            max_instances=3,
            id="check_scheduled_emails",
            args=[app],
        )
        try:
            scheduler.start()
            logging.info("🚀 Scheduler avviato con successo!")
        except Exception as error:
            logging.info(f"⚠️ Scheduler già in esecuzione: {error}")
    else:
        logging.info("⚠️ Scheduler era già attivo, nessuna azione necessaria.")

    return scheduler
