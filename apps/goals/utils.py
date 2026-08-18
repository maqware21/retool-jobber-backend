import datetime

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
    """The 1st of the current calendar month, as a date."""
    return datetime.date.today().replace(day=1)
