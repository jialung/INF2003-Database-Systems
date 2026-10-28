import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-only-secret")
    DATAMALL_KEY = os.getenv("DATAMALL_KEY", "")

    MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_DB = os.getenv("MYSQL_DB", "transport")
    MYSQL_ROOT_PASSWORD = os.getenv("MYSQL_ROOT_PASSWORD", "rootpass")
    MYSQL_RW_USER = os.getenv("MYSQL_RW_USER", "app_rw")
    MYSQL_RW_PASSWORD = os.getenv("MYSQL_RW_PASSWORD", "app_rw_pass")
    MYSQL_RO_USER = os.getenv("MYSQL_RO_USER", "app_ro")
    MYSQL_RO_PASSWORD = os.getenv("MYSQL_RO_PASSWORD", "app_ro_pass")

    MONGO_URI = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
    MONGO_DB = os.getenv("MONGO_DB", "transport")

    COLLECTOR_INTERVAL = int(os.getenv("COLLECTOR_INTERVAL", "90"))
    COLLECTOR_STOP_COUNT = int(os.getenv("COLLECTOR_STOP_COUNT", "100"))
