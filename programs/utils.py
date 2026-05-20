import logging
import os
import secrets
import string
from datetime import date, datetime
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import send_mail
from django.core.signing import BadSignature, SignatureExpired, Signer, TimestampSigner
from django.urls import reverse

logger = logging.getLogger(__name__)


def generate_otp(length=6):
    return "".join(secrets.choice(string.digits) for _ in range(length))


def send_otp_email(email, otp):
    subject = "Your GoS Admin Portal Verification Code"
    message = (
        f"Your verification code is: {otp}\n\nThis code will expire in 10 minutes."
    )
    from_email = settings.DEFAULT_FROM_EMAIL
    name = getattr(settings, "DEFAULT_FROM_NAME", None)
    if name:
        from_email = f'"{name}" <{from_email}>'
    send_mail(subject, message, from_email, [email])


def generate_signed_parent_url(application_id):
    signer = TimestampSigner()
    token = signer.sign(str(application_id))
    # We'll define the URL name later, for now using a placeholder
    return reverse("apply_parent_resume", kwargs={"token": token})


def verify_signed_parent_token(token, max_age=86400):  # 24 hours
    signer = TimestampSigner()
    try:
        application_id = signer.unsign(token, max_age=max_age)
        return application_id
    except (BadSignature, SignatureExpired):
        return None


# ---------------------------------------------------------------------------
# Image normalization
# ---------------------------------------------------------------------------


def normalize_image_field(file_field, *, quality=85, log_prefix="image"):
    """Re-encode an uploaded ImageField as a normalized RGB JPEG, in place.

    - No-op when the field is empty or already committed (already-stored files).
    - Fixes EXIF orientation, converts to RGB, optimizes JPEG output.
    - Falls back to a HEIC/HEIF-aware path if the primary decode fails.
    - Never raises: any unexpected error is logged and the original file is left
      untouched so the surrounding ``Model.save()`` can proceed.

    Returns True if the field was rewritten, False otherwise.
    """
    if not file_field:
        return False
    # Lazy imports so utils stays importable in lightweight contexts.
    try:
        from PIL import Image, ImageOps
    except ImportError:
        logger.debug("Pillow unavailable; skipping %s normalization", log_prefix)
        return False

    try:
        # Only process a newly assigned upload (uncommitted) or anything with an accessible file handle
        if getattr(file_field, "_committed", True) and not hasattr(file_field, "file"):
            return False
        try:
            f = getattr(file_field, "file", file_field)
            try:
                f.seek(0)
            except (AttributeError, IOError):
                pass
            img = Image.open(f)
            img.load()
            try:
                img = ImageOps.exif_transpose(img)
            except (AttributeError, TypeError, IndexError):
                pass
            if img.mode != "RGB":
                img = img.convert("RGB")
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=quality, optimize=True)
            buffer.seek(0)
            base, _ = os.path.splitext(file_field.name or "photo")
            new_name = f"{base}.jpg"
            file_field.save(new_name, ContentFile(buffer.read()), save=False)
            return True
        except Exception:
            # Fallback: legacy HEIC/HEIF detection and conversion
            try:
                name_lower = (file_field.name or "").lower()
                needs_convert_by_ext = name_lower.endswith(
                    ".heic"
                ) or name_lower.endswith(".heif")
                convert = needs_convert_by_ext
                if not convert:
                    try:
                        file_field.open("rb")
                        img_probe = Image.open(file_field)
                        fmt = (img_probe.format or "").upper()
                        img_probe.close()
                        if fmt in ("HEIC", "HEIF"):
                            convert = True
                    except Exception:
                        convert = needs_convert_by_ext
                    finally:
                        try:
                            file_field.close()
                        except (AttributeError, IOError):
                            pass
                if convert:
                    file_field.open("rb")
                    img = Image.open(file_field)
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    buffer = BytesIO()
                    img.save(buffer, format="JPEG", quality=quality, optimize=True)
                    buffer.seek(0)
                    base, _ = os.path.splitext(file_field.name or "photo")
                    new_name = f"{base}.jpg"
                    file_field.save(new_name, ContentFile(buffer.read()), save=False)
                    try:
                        file_field.close()
                    except (AttributeError, IOError):
                        pass
                    return True
            except Exception:
                logger.debug("%s normalization failed", log_prefix, exc_info=True)
    except Exception:
        logger.debug("Unexpected error in %s processing", log_prefix, exc_info=True)
    return False


# ---------------------------------------------------------------------------
# Alumni conversion
# ---------------------------------------------------------------------------


def find_matching_alumni_adult(student):
    """Return an existing Adult that likely represents ``student`` as alumni.

    Match order:
      1. ``Adult.alumni_email`` (case-insensitive) matching the student's
         personal or Andrew email.
      2. ``Adult.email`` (case-insensitive) matching with ``is_alumni=True``.
      3. First/last name match with ``is_alumni=True``.
    Returns None if no match is found.
    """
    from .models import Adult  # local import to avoid circulars

    emails = [
        getattr(student, "personal_email", None),
        getattr(student, "andrew_email", None),
    ]
    for e in emails:
        if e:
            a = Adult.objects.filter(alumni_email__iexact=e).first()
            if a:
                return a
            a = Adult.objects.filter(email__iexact=e, is_alumni=True).first()
            if a:
                return a
    first = (
        getattr(student, "first_name", None)
        or getattr(student, "legal_first_name", None)
        or ""
    ).strip()
    last = (getattr(student, "last_name", None) or "").strip()
    if first and last:
        return Adult.objects.filter(
            first_name__iexact=first, last_name__iexact=last, is_alumni=True
        ).first()
    return None


def convert_student_to_alumni(student):
    """Idempotently convert a Student into an alumni Adult record.

    Side effects:
      - Creates a new ``Adult`` (with ``is_alumni=True``) when no matching
        record is found, or updates the existing one's ``is_alumni`` /
        ``alumni_email`` fields when needed.
      - Marks the student as ``graduated=True`` if not already.

    Returns a tuple ``(adult, created, marked_graduated)``.
    """
    from .models import Adult  # local import to avoid circulars

    adult = find_matching_alumni_adult(student)
    created = False
    if adult is None:
        adult = Adult.objects.create(
            first_name=student.first_name or student.legal_first_name or "",
            last_name=student.last_name or "",
            alumni_email=student.personal_email or student.andrew_email,
            is_alumni=True,
        )
        created = True
    else:
        changed = False
        if not adult.is_alumni:
            adult.is_alumni = True
            changed = True
        if not adult.alumni_email and (student.personal_email or student.andrew_email):
            adult.alumni_email = student.personal_email or student.andrew_email
            changed = True
        if changed:
            adult.save(update_fields=["is_alumni", "alumni_email", "updated_at"])

    marked_graduated = False
    if not student.graduated:
        student.graduated = True
        student.save(update_fields=["graduated", "updated_at"])
        marked_graduated = True
    return adult, created, marked_graduated


# ---------------------------------------------------------------------------
# CSV / XLSX row helpers (shared by import views)
# ---------------------------------------------------------------------------


def row_raw(d, *keys):
    """Return the first non-None value among ``keys`` from dict ``d``."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def row_val(d, *keys):
    """Return the first non-empty trimmed string value among ``keys``.

    Treats the literal string ``"none"`` (case-insensitive) as empty.
    """
    for k in keys:
        if k in d and d[k] is not None:
            v = str(d[k]).strip()
            if v != "" and v.lower() != "none":
                return v
    return None


def row_val_bool(d, *keys):
    """Parse a boolean from common truthy/falsy spellings; None if absent/unknown."""
    v = row_val(d, *keys)
    if v is None:
        return None
    s = v.strip().lower()
    if s in ("y", "yes", "true", "t", "1"):
        return True
    if s in ("n", "no", "false", "f", "0"):
        return False
    return None


def row_val_date(d, *keys):
    """Parse a date from a date/datetime object or common string formats."""
    rv = row_raw(d, *keys)
    if isinstance(rv, datetime):
        return rv.date()
    if isinstance(rv, date):
        return rv
    v = row_val(d, *keys)
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None
