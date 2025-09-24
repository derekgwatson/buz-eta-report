# config.py
import logging


class BaseConfig:
    SECRET_KEY = "change-me"
    # your existing settings…
    LOG_LEVEL = logging.INFO
    TEMPLATES_AUTO_RELOAD = False
    PROPAGATE_EXCEPTIONS = False
    TRAP_HTTP_EXCEPTIONS = False
    DEBUG_SQL = False          # <- custom (see get_db)
    RAISE_ON_DB_ERROR = False


class DevConfig(BaseConfig):
    DEBUG = True
    ENV = "development"
    LOG_LEVEL = logging.DEBUG
    TEMPLATES_AUTO_RELOAD = True
    PROPAGATE_EXCEPTIONS = True
    TRAP_HTTP_EXCEPTIONS = True
    DEBUG_SQL = True
    RAISE_ON_DB_ERROR = True  # fail fast in dev
    RUN_REPORTS_INLINE = True


class ProdConfig(BaseConfig):
    DEBUG = False
    ENV = "production"
    LOG_LEVEL = logging.INFO
    RUN_REPORTS_INLINE = False


class StagingConfig(BaseConfig):
    DEBUG = False
    ENV = "staging"
    LOG_LEVEL = logging.DEBUG
    RUN_REPORTS_INLINE = False
