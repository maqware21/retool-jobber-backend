"""
Throwaway-database settings override for testing hand-written migrations
locally, with ZERO connection to the real server or real data.

This project's migrations are hand-written (not `makemigrations`-
generated) per standing instruction — the assistant never runs
makemigrations/migrate against the real database. That discipline
previously meant a hand-written migration's *internal consistency* (do
its CreateModel/AddConstraint/AddField operations actually agree with
each other?) was only ever checked by reading it, not by running it.
That's how the 0002 techniciannualgoal/technicianannualgoal typo shipped
undetected.

Going forward: run `makemigrations --check` and a real `migrate` against
THIS settings module (a scratch SQLite file, not Postgres) before
reporting any hand-written migration as done. This module changes
nothing else — same INSTALLED_APPS, same everything — only DATABASES is
overridden, so it exercises the exact same migration graph the real
server will apply.

Usage:
    DJANGO_SETTINGS_MODULE=tech_track_pro.settings_migration_test \
        python manage.py makemigrations --check --dry-run
    DJANGO_SETTINGS_MODULE=tech_track_pro.settings_migration_test \
        python manage.py migrate
    rm db_migration_test.sqlite3   # afterwards -- pure scratch, not committed
"""
from .settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db_migration_test.sqlite3',
    }
}
