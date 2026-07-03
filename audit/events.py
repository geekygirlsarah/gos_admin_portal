from django.db import models


class AuditEvent(models.TextChoices):
    # --- Student Records ---
    ADMISSION_DECISION = "ADMISSION_DECISION", "Admission decision made"
    CONTACT_INFO_UPDATED = "CONTACT_INFO_UPDATED", "Contact/address info updated"
    GUARDIAN_ADDED = "GUARDIAN_ADDED", "Guardian/parent added to student"
    GUARDIAN_REMOVED = "GUARDIAN_REMOVED", "Guardian/parent removed from student"
    ENROLLMENT_CHANGED = "ENROLLMENT_CHANGED", "Enrollment status changed"

    # --- Account & Access ---
    ACCOUNT_CREATED = "ACCOUNT_CREATED", "User account created"
    ACCOUNT_DEACTIVATED = "ACCOUNT_DEACTIVATED", "User account deactivated"
    ROLE_CHANGED = "ROLE_CHANGED", "User role changed"
    PASSWORD_RESET = "PASSWORD_RESET", "Password reset by admin"

    # --- Authentication ---
    USER_LOGIN = "USER_LOGIN", "User logged in"
    USER_LOGOUT = "USER_LOGOUT", "User logged out"
    LOGIN_FAILED = "LOGIN_FAILED", "Login attempt failed"

    # --- Sensitive Data Access ---
    SENSITIVE_DATA_VIEW = "SENSITIVE_DATA_VIEW", "Sensitive data viewed by mentor"
