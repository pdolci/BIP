import logging
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
from models import ReadingSchedule
from extensions import db
from email_sender import send_next_book_part
from schedule_utils import compute_next_send_datetime
from time_utils import utc_now_naive

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

scheduler = None  # Variabile globale per lo scheduler

def check_scheduled_emails(app: Flask):
    """ Controlla e invia email ai lettori, con il contesto Flask passato come argomento """
    logging.info("✅ Job check_scheduled_emails AVVIATO")

    with app.app_context():  # ✅ Creiamo manualmente il contesto Flask
        now = utc_now_naive()
        logging.info(f"🕒 Checking schedules at {now}")
        
        db.session.expire_all()
        
        # 🔴 DEBUG: Log all rows in `reading_schedule` before filtering
        all_schedules = db.session.query(ReadingSchedule).all()
        logging.info(f"🔍 Total schedules in DB: {len(all_schedules)}")
        for sched in all_schedules:
            logging.info(f"🔍 ID: {sched.id} | User: {sched.user_id} | Book: {sched.book_id} | Next Send: {sched.next_send_date}")

        # Fetch fresh data from the database with filtering
        schedules = db.session.query(ReadingSchedule).filter(
            ReadingSchedule.next_send_date <= now,
            ReadingSchedule.is_paused.is_(False),
            db.or_(ReadingSchedule.travel_pause_until.is_(None), ReadingSchedule.travel_pause_until <= now),
        ).all()
        
        # 🔴 DEBUG: Log how many schedules were found
        logging.info(f"📊 Found {len(schedules)} schedules due for sending.")

        if not schedules:
            logging.info("⚠️ Nessun programma di lettura trovato con next_send_date passato.")
            return

        for schedule in schedules:
            logging.info(f"📨 Inviando email per il libro ID {schedule.book_id} all'utente {schedule.user_id}")
            needs_commit = False

            if schedule.travel_pause_until and now < schedule.travel_pause_until:
                if schedule.next_send_date and schedule.next_send_date < schedule.travel_pause_until:
                    schedule.next_send_date = schedule.travel_pause_until
                    needs_commit = True
                if needs_commit:
                    db.session.commit()
                continue

            if schedule.travel_pause_until and now >= schedule.travel_pause_until:
                schedule.travel_pause_until = None
                needs_commit = True

            if schedule.snooze_until and now < schedule.snooze_until:
                if schedule.next_send_date and schedule.next_send_date < schedule.snooze_until:
                    schedule.next_send_date = schedule.snooze_until
                    needs_commit = True
                if needs_commit:
                    db.session.commit()
                continue

            if schedule.snooze_until and now >= schedule.snooze_until:
                schedule.snooze_until = None
                needs_commit = True

            if schedule.skip_next:
                schedule.skip_next = False
                schedule.next_send_date = compute_next_send_datetime(now, schedule, allow_immediate=False)
                db.session.commit()
                continue

            if needs_commit:
                db.session.commit()

            send_next_book_part(schedule.id)

    logging.info("✅ Job check_scheduled_emails COMPLETATO")

def start_scheduler(app: Flask):
    """ Crea e avvia lo scheduler solo se non è già in esecuzione """
    global scheduler
    if scheduler is None:  # ✅ Controlla se lo scheduler esiste già
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            check_scheduled_emails,
            "interval",
            minutes=1,
            max_instances=3,
            id="check_scheduled_emails",
            args=[app]  # ✅ Passiamo `app` come argomento al job
        )
        logging.info("🚀 Job 'check_scheduled_emails' aggiunto allo scheduler!")

        try:
            scheduler.start()
            logging.info("🚀 Scheduler avviato con successo!")
        except Exception as e:
            logging.info(f"⚠️ Scheduler già in esecuzione: {e}")

    else:
        logging.info("⚠️ Scheduler era già attivo, nessuna azione necessaria.")
    
    return scheduler

