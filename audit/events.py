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
    PROFILE_LINK_CHANGED = (
        "PROFILE_LINK_CHANGED",
        "User account linked or unlinked from profile",
    )

    # --- Authentication ---
    USER_LOGIN = "USER_LOGIN", "User logged in"
    USER_LOGOUT = "USER_LOGOUT", "User logged out"
    LOGIN_FAILED = "LOGIN_FAILED", "Login attempt failed"

    # --- Data Merge ---
    RECORDS_MERGED = "RECORDS_MERGED", "Duplicate records merged"

    # --- Deletion ---
    RECORD_DELETED = "RECORD_DELETED", "Record deleted"

    # --- Sensitive Data Access ---
    SENSITIVE_DATA_VIEW = "SENSITIVE_DATA_VIEW", "Sensitive data viewed by mentor"

    # --- Email & Communication ---
    EMAIL_DELIVERY_FAILED = "EMAIL_DELIVERY_FAILED", "Email delivery issue received"
    EMAIL_COMPLAINT = "EMAIL_COMPLAINT", "Spam complaint received"

    # --- Badges ---
    BADGE_AWARDED = "BADGE_AWARDED", "Badge awarded to student"
    BADGE_REVOKED = "BADGE_REVOKED", "Badge revoked from student"
    BADGE_CREATED = "BADGE_CREATED", "Badge created"
    BADGE_UPDATED = "BADGE_UPDATED", "Badge updated"
    BADGE_DELETED = "BADGE_DELETED", "Badge deleted"

    # --- Multi-Tenant & Platform Admin ---
    USER_IMPERSONATED = "USER_IMPERSONATED", "User impersonated by superuser"
    IMPERSONATION_ENDED = "IMPERSONATION_ENDED", "User impersonation ended"
    ORGANIZATION_CREATED = "ORGANIZATION_CREATED", "Organization created"
    ORGANIZATION_UPDATED = "ORGANIZATION_UPDATED", "Organization updated"
    ORGANIZATION_STATUS_CHANGED = (
        "ORGANIZATION_STATUS_CHANGED",
        "Organization status toggled",
    )
