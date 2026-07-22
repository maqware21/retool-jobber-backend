MESSAGES = dict(
    # ── Generic ──────────────────────────────────────────────────────────────
    SUCCESS="Success",
    CREATED="{} created successfully",
    UPDATED="{} updated successfully",
    DELETE="{} deleted successfully",
    REQUIRED="{} is required",
    SOMETHING_WENT_WRONG="Something went wrong. Please try again.",
    NON_EMPTY_FIELD="This field may not be blank.",
    OBJ_NOT_FOUND_ERROR="{} not found",
    PARAMETER_MISSING="Required parameter is missing",
    PERMISSION_ERROR="You do not have permission to perform this action.",
    CANT_PERFORM_ACTION="You cannot perform this action.",

    # ── Auth / User ───────────────────────────────────────────────────────────
    EMAIL_EXIST="An account with this email already exists.",
    ENTER_EMAIL="Please enter your email address.",
    ENTER_FIRST_NAME="Please enter your first name.",
    ENTER_LAST_NAME="Please enter your last name.",
    ENTER_PASSWORD="Please enter your password.",
    INVALID_USER_EMAIL_CREDENTIALS="No account found with this email. Please check and try again.",
    INVALID_USER_PASSWORD_CREDENTIALS="Incorrect password. Please check and try again.",
    INVALID_CREDENTIALS="Invalid email or password.",
    USER_NOT_FOUND="User not found.",
    ACCOUNT_INACTIVE="Your account is inactive. Please contact support.",
    EMAIL_NOT_FOUND="No account found with this email address.",
    SELECT_VALID_COMBINATION="Please select a valid role.",

    # ── Password ──────────────────────────────────────────────────────────────
    PASSWORD_WEAK="Password must be at least 8 characters and include at least one digit and one uppercase letter.",
    PASSWORD_MISMATCH="Old password is incorrect.",
    PASSWORD_RESET_COMING_SOON="Password reset via email is not yet enabled. Please contact support.",

    # ── Token ─────────────────────────────────────────────────────────────────
    INVALID_TOKEN="Token is invalid or expired.",
    TOKEN_BLACKLISTED="Token has already been invalidated.",
)
