"""Minimal Django settings for the django_app demo (see test_models.py).

In-memory sqlite: the database lives and dies with the test process, so no
files are ever written to the repo.
"""

SECRET_KEY = "pytest-deck-demo"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "polls",
]

DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
}

USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
