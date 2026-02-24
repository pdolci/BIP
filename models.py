import os
from extensions import db
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config

class User(db.Model):
    ROLE_USER = "user"
    ROLE_CONTENT_MANAGER = "content_manager"
    ROLE_ADMIN = "admin"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    role = db.Column(db.String(30), nullable=False, default=ROLE_USER)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    telegram_handle = db.Column(db.String(120), nullable=True)
    preferred_delivery_channel = db.Column(db.String(20), nullable=False, default="email")
    email_confirmed = db.Column(db.Boolean, nullable=False, default=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def resolved_role(self):
        if self.role in {self.ROLE_USER, self.ROLE_CONTENT_MANAGER, self.ROLE_ADMIN}:
            return self.role
        return self.ROLE_ADMIN if self.is_admin else self.ROLE_USER

    def can_manage_books(self):
        return self.resolved_role in {self.ROLE_CONTENT_MANAGER, self.ROLE_ADMIN}

    def can_manage_admin_panel(self):
        return self.resolved_role == self.ROLE_ADMIN

class Book(db.Model):
    __table_args__ = (
        db.Index("ix_book_is_active_title", "is_active", "title"),
    )

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    short_description = db.Column(db.Text, nullable=True)
    author = db.Column(db.String(255), nullable=True)
    publication_year = db.Column(db.Integer, nullable=True)
    genre = db.Column(db.String(120), nullable=True)
    tags = db.Column(db.String(500), nullable=True)
    language = db.Column(db.String(80), nullable=True)
    estimated_reading_hours = db.Column(db.Float, nullable=True)
    cover_image = db.Column(db.String(500), nullable=True)
    word_count = db.Column(db.Integer, nullable=False, default=0)

    def get_absolute_path(self):
        """Returns the absolute file path of the book."""
        return os.path.join(Config.UPLOAD_FOLDER, self.file_path)

class ReadingSchedule(db.Model):
    __table_args__ = (
        db.Index("ix_reading_schedule_user_active", "user_id", "is_paused"),
        db.Index("ix_reading_schedule_due", "next_send_date", "is_paused", "travel_pause_until"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    book_id = db.Column(db.Integer, db.ForeignKey("book.id"), nullable=False)
    words_per_minute = db.Column(db.Integer, default=200)
    minutes_per_reading = db.Column(db.Integer, nullable=False)
    last_sent_index = db.Column(db.Integer, default=0)
    frequency_days = db.Column(db.Integer, nullable=False)
    frequency_type = db.Column(db.String(20), nullable=True)
    weekdays = db.Column(db.String(20), nullable=True)
    frequency_mode = db.Column(db.String(20), nullable=False, default="interval")
    frequency_weekdays = db.Column(db.String(20), nullable=True)
    delivery_time = db.Column(db.Time, nullable=True)
    next_send_date = db.Column(db.DateTime, nullable=False)
    is_paused = db.Column(db.Boolean, default=False) 
    skip_next = db.Column(db.Boolean, default=False)
    snooze_until = db.Column(db.DateTime, nullable=True)
    travel_pause_until = db.Column(db.DateTime, nullable=True)
    delivery_channel = db.Column(db.String(20), nullable=False, default="email")
    book = db.relationship("Book", backref="schedules", lazy=True)
    user = db.relationship("User", backref="schedules", lazy=True)


class DeliveryEvent(db.Model):
    __table_args__ = (
        db.Index("ix_delivery_event_schedule_created", "schedule_id", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey("reading_schedule.id"), nullable=False)
    event_type = db.Column(db.String(20), nullable=False, default="sent")
    words_count = db.Column(db.Integer, nullable=False, default=0)
    start_word_index = db.Column(db.Integer, nullable=True)
    end_word_index = db.Column(db.Integer, nullable=True)
    note = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp(), nullable=False)

    schedule = db.relationship("ReadingSchedule", backref=db.backref("delivery_events", lazy=True, cascade="all, delete-orphan"), lazy=True)
