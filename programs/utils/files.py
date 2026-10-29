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


def build_upload_path(
    prefix: str,
    instance=None,
    filename: str = "",
    id_attr: str | None = None,
    default_id: str = "unassigned",
    sanitize: bool = True,
) -> str:
    """Build a standardized upload path for Model FileFields/ImageFields.

    Example flat:
        build_upload_path("photos/students", filename="pic.jpg")
        -> "photos/students/pic.jpg"

    Example scoped:
        build_upload_path("student_documents", instance=doc, filename="waiver.pdf", id_attr="student_id")
        -> "student_documents/12/waiver.pdf"
    """
    clean_name = sanitize_upload_filename(filename) if sanitize else filename
    prefix = prefix.strip("/")

    if id_attr:
        scope_val = getattr(instance, id_attr, None) if instance is not None else None
        scope_str = str(scope_val) if scope_val not in (None, "") else default_id
        return f"{prefix}/{scope_str}/{clean_name}"

    return f"{prefix}/{clean_name}"


def scoped_upload_path_builder(
    prefix: str,
    id_attr: str | None = None,
    default_id: str = "unassigned",
    sanitize: bool = True,
):
    """Factory creating an upload_to callable compatible with Django FileField."""

    def _upload_to(instance, filename: str) -> str:
        return build_upload_path(
            prefix=prefix,
            instance=instance,
            filename=filename,
            id_attr=id_attr,
            default_id=default_id,
            sanitize=sanitize,
        )

    return _upload_to
