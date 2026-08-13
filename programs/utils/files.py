import os
import uuid

from django.utils.text import get_valid_filename


def sanitize_upload_filename(filename, max_length=150):
    """
    Sanitize and truncate an uploaded filename.

    1. Removes illegal characters using Django's get_valid_filename.
    2. Truncates the base name if it's too long, preserving the extension.
    3. Adds a small random suffix if truncation was needed to avoid collisions.
    """
    if not filename:
        return filename

    base, ext = os.path.splitext(filename)

    # 1. Django sanitization
    base = get_valid_filename(base)

    # 2. Truncate if needed
    # We want base + ext to be <= max_length
    allowed_base_len = max_length - len(ext)

    if len(base) > allowed_base_len:
        # Use a short hash/uuid to keep it unique-ish after truncation
        suffix = f"_{uuid.uuid4().hex[:6]}"
        # Ensure we have enough room for the suffix
        if allowed_base_len < len(suffix):
            # Extreme case: extension + suffix is almost max_length
            base = suffix[:allowed_base_len]
        else:
            base = base[: allowed_base_len - len(suffix)] + suffix

    return f"{base}{ext}"
