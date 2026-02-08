import os

class Config:
    SECRET_KEY = "your_secret_key"
    
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "books")

    
    SQLALCHEMY_DATABASE_URI = "mysql+pymysql://booksinpieces:prova99@localhost/book_reader?charset=utf8mb4"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAIL_SERVER = "smtp.gmail.com"
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USERNAME = "enigmadolci@gmail.com"
    MAIL_PASSWORD = "vhxd seyr sgqi rljb"
    #MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "default_email")
    #MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "default_password")
    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Europe/Rome")
