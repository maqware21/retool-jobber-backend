DEFAULT_LIMIT = 20
MAX_PAGE_SIZE = 100
MAX_OFFSET = 100
MIN_LIMIT = 1
MIN_OFFSET = 0

# Roles stored as Django Permission objects (codename, name).
# Index positions are used in user_permissions.py — do not reorder.
# Index 0 = admin, Index 1 = customer
USER_PERMISSIONS = [
    ('admin', 'admin'),
    ('customer', 'customer'),
]
