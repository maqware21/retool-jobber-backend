import datetime

from django.utils import timezone
from rest_framework.exceptions import ValidationError


def parse_month(value):
    """
    Accepts a month as 'YYYY-MM' (the format both goals endpoints take on
    input, per TL decision -- simpler and less error-prone for whatever
    frontend eventually calls this than requiring a full 'YYYY-MM-01'
    date) and returns a date for the 1st of that month, which is what
    TeamGoal.month / TechnicianGoal.month actually store.

    Raises a DRF ValidationError (not Django's) on any other shape, so it
    flows through the same validator_errors() handling as every other
    view-level error in this project.
    """
    try:
        parsed = datetime.datetime.strptime(value, '%Y-%m')
    except (ValueError, TypeError):
        raise ValidationError({'month': ["month must be in 'YYYY-MM' format."]})
    return datetime.date(parsed.year, parsed.month, 1)


def current_month():
    """
    The 1st of the current calendar month, as a date.

    Uses timezone.localdate() (Django's own now(), converted to
    settings.TIME_ZONE = 'UTC'), NOT datetime.date.today() (2026-08-21
    fix, found while reviewing current_year() below for the same
    concern). date.today() reads the server PROCESS's system clock in
    whatever timezone the OS itself is configured for -- correct only as
    long as that happens to also be UTC, an assumption that was
    previously true-but-unverified rather than actually guaranteed.
    timezone.localdate() removes that dependency entirely: it's always
    correct relative to Django's own configured timezone, regardless of
    the underlying OS clock's own timezone setting.
    """
    return timezone.localdate().replace(day=1)


def parse_year(value):
    """
    Accepts a year as 'YYYY' (same short-input convention as
    parse_month()'s 'YYYY-MM') and returns a date for Jan 1 of that year,
    which is what TeamAnnualGoal.year / TechnicianAnnualGoal.year
    actually store.

    Raises a DRF ValidationError (not Django's) on any other shape, same
    reasoning as parse_month().
    """
    try:
        parsed = datetime.datetime.strptime(value, '%Y')
    except (ValueError, TypeError):
        raise ValidationError({'year': ["year must be in 'YYYY' format."]})
    return datetime.date(parsed.year, 1, 1)


def current_year():
    """
    Jan 1 of the current calendar year, as a date.

    Same fix as current_month() above, same reasoning: timezone.
    localdate(), not datetime.date.today() -- a year boundary has the
    identical (if twelve-times-rarer) risk a month boundary does, and
    this function is used server-side both to default GET's ?year= and,
    in technician_stats.py, to window the year-to-date revenue queryset
    -- a wrong "what year is it" there would silently compare a
    technician's real YTD revenue against the WRONG year's annual goal.
    """
    return timezone.localdate().replace(month=1, day=1)
