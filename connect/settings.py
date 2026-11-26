"""Everclass Connect settings. Every secret comes from the environment (Railway variables)."""

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        raise ImproperlyConfigured(f"Set the {name} environment variable.")
    return value


def env_bool(name, default=False):
    return str(os.environ.get(name, default)).lower() in {"1", "true", "yes", "on"}


DEBUG = env_bool("DEBUG", False)
SECRET_KEY = env("SECRET_KEY", "dev-only-insecure-key" if DEBUG else None, required=True)
ALLOWED_HOSTS = [h.strip() for h in env("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "whatsapp",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "connect.urls"
WSGI_APPLICATION = "connect.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}", conn_max_age=60
    )
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "en"
TIME_ZONE = "Asia/Kolkata"
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# --- Meta WhatsApp Cloud API ---
META_GRAPH_VERSION = env("META_GRAPH_VERSION", "v25.0")
META_GRAPH_BASE_URL = env("META_GRAPH_BASE_URL", "https://graph.facebook.com")
# App Secret signs every webhook (X-Hub-Signature-256). Needed by the web service.
META_APP_SECRET = env("META_APP_SECRET", "")
# Any long random string; paste the same value into the Meta webhook settings.
META_WEBHOOK_VERIFY_TOKEN = env("META_WEBHOOK_VERIFY_TOKEN", "")
META_HTTP_TIMEOUT_SECONDS = float(env("META_HTTP_TIMEOUT_SECONDS", "15"))

# Fernet key used to encrypt access tokens at rest. Generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", "")

# Worker
WORKER_POLL_SECONDS = float(env("WORKER_POLL_SECONDS", "2"))
WORKER_BATCH_SIZE = int(env("WORKER_BATCH_SIZE", "20"))
MESSAGE_MAX_ATTEMPTS = int(env("MESSAGE_MAX_ATTEMPTS", "6"))
DEFAULT_COUNTRY_CODE = env("DEFAULT_COUNTRY_CODE", "91")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
