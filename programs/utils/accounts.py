"""Account-link helpers shared across profile types (Student/Adult)."""


def transfer_user_account(keep, source):
    """Move ``source``'s linked user account to ``keep`` if ``keep`` has none.

    Returns True if ``keep.user`` was updated.
    """
    if keep.user_id or not source.user_id:
        return False
    source_user = source.user
    source.user = None
    source.save(update_fields=["user"])
    keep.user = source_user
    return True
