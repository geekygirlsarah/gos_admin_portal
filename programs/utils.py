import logging
import os
import secrets
import string
from datetime import date, datetime
from decimal import ROUND_HALF_DOWN, Decimal
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import send_mail
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
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
      1. ``Adult.student_record`` matching the student.
      2. ``Adult.personal_email`` (case-insensitive) matching the student's
         personal or Andrew email.
      3. ``MentorAndrewAccess.andrew_email`` (case-insensitive) matching the student's andrew_email.
      4. First/last name match with ``is_alumni=True``.
    Returns None if no match is found.
    """
    from .models import Adult  # local import to avoid circulars

    # 1. Direct link
    if student.pk:
        a = Adult.objects.filter(student_record=student).first()
        if a:
            return a

    first = (
        getattr(student, "first_name", None)
        or getattr(student, "legal_first_name", None)
        or ""
    ).strip()
    last = (getattr(student, "last_name", None) or "").strip()

    # 2. Emails
    emails = [
        getattr(student, "personal_email", None),
        getattr(student, "andrew_email", None),
    ]
    for e in emails:
        if e:
            # personal_email match with name check to avoid false parent matches
            if first and last:
                a = Adult.objects.filter(
                    personal_email__iexact=e,
                    first_name__iexact=first,
                    last_name__iexact=last,
                ).first()
                if a:
                    return a
            # Andrew email match
            a = Adult.objects.filter(andrew_email__iexact=e).first()
            if a:
                return a

    # 3. Name match if already flagged as alumni
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
        ``personal_email`` field when needed.
      - Links the ``Adult`` record back to the ``Student`` via ``student_record``.
      - Transfers the ``User`` link from ``Student`` to ``Adult`` if applicable.
      - Marks the student as ``graduated=True``.

    Returns a tuple ``(adult, created, marked_graduated)``.
    """
    from .models import Adult  # local import to avoid circulars

    adult = find_matching_alumni_adult(student)
    created = False
    if adult is None:
        adult = Adult.objects.create(
            first_name=student.legal_first_name or "",
            preferred_first_name=student.first_name,
            last_name=student.last_name or "",
            pronouns=student.pronouns,
            address=student.address,
            city=student.city,
            state=student.state,
            zip_code=student.zip_code,
            cell_phone=student.cell_phone_number,
            personal_email=student.personal_email or student.andrew_email,
            is_alumni=True,
            student_record=student,
            photo=student.photo,
        )
        # Copy Andrew ID details if the student had them
        if student.andrew_id or student.andrew_email:
            adult.andrew_id = adult.andrew_id or student.andrew_id or None
            adult.andrew_email = adult.andrew_email or student.andrew_email or None
            adult.save(update_fields=["andrew_id", "andrew_email", "updated_at"])
        created = True
    else:
        changed = False
        if not adult.is_alumni:
            adult.is_alumni = True
            changed = True
        if adult.student_record_id != student.id:
            adult.student_record = student
            changed = True
        if not adult.personal_email and (
            student.personal_email or student.andrew_email
        ):
            adult.personal_email = student.personal_email or student.andrew_email
            changed = True

        # Copy missing fields from student to adult
        fields_to_copy = {
            "preferred_first_name": "first_name",
            "pronouns": "pronouns",
            "address": "address",
            "city": "city",
            "state": "state",
            "zip_code": "zip_code",
            "cell_phone": "cell_phone_number",
            "personal_email": "personal_email",
        }
        for adult_field, student_field in fields_to_copy.items():
            if not getattr(adult, adult_field) and getattr(student, student_field):
                setattr(adult, adult_field, getattr(student, student_field))
                changed = True

        # Copy Andrew ID details if student had them and adult doesn't yet
        if student.andrew_id or student.andrew_email:
            access_changed = False
            if not adult.andrew_id and student.andrew_id:
                adult.andrew_id = student.andrew_id
                access_changed = True
            if not adult.andrew_email and student.andrew_email:
                adult.andrew_email = student.andrew_email
                access_changed = True
            if access_changed:
                changed = True

        if not adult.photo and student.photo:
            adult.photo = student.photo
            changed = True

        if changed:
            adult.save()

    marked_graduated = False
    student_changed = False
    if not student.graduated:
        student.graduated = True
        student_changed = True
        marked_graduated = True

    if student.user and not adult.user:
        user = student.user
        student.user = None
        student_changed = True
        adult.user = user
        adult.save(update_fields=["user"])

    if student_changed:
        student.save(update_fields=["graduated", "user", "updated_at"])

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


def get_academic_year_ending(today: date = None) -> int:
    """Return the academic year ending (e.g., 2025 for 2024-25 school year).
    July 1 rollover:
    - before July 1: academic year ending = current year
    - on/after July 1: academic year ending = next year
    """
    if today is None:
        today = date.today()
    if today.month < 7:
        return today.year
    else:
        return today.year + 1


def calculate_grade(graduation_year: int, ref_date: date = None) -> int | None:
    """Return the student's current grade as an integer (0=K, 1–12) based on
    graduation year and reference date.
    - July 1 rollover (via get_academic_year_ending).
    - Returns None if the student has already graduated (calculated grade > 12).
    - Grades below 0 are clamped to 0 (Kindergarten).
    """
    if not graduation_year:
        return None
    academic_year_ending = get_academic_year_ending(ref_date)
    grade = 12 - (graduation_year - academic_year_ending)
    if grade > 12:
        return None
    return max(0, grade)


def calculate_graduation_year(grade: int | str, ref_date: date = None) -> int:
    """Return the expected graduation year based on current grade and reference date.
    - July 1 rollover (via get_academic_year_ending).
    """
    academic_year_ending = get_academic_year_ending(ref_date)
    return academic_year_ending + (12 - int(grade))


def format_grade(grade: int | str | None) -> str:
    """Return a human-readable grade label: 'K', '1st Grade', …, '12th Grade',
    'Graduated', or '—' if grade is None.
    """
    if grade is None or grade == "":
        return "—"

    try:
        n = int(grade)
    except (ValueError, TypeError):
        return str(grade)

    if n > 12:
        return "Graduated"
    if n == 0:
        return "K"

    if 10 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix} Grade"


def get_safe_url(request, url):
    """
    Return a safe local URL for redirects.
    Accepts relative URLs and same-host absolute URLs, and always returns a
    relative path (or None if unsafe/not present).
    """
    url = str(url).strip()
    if not url:
        return None

    from urllib.parse import urlsplit

    from django.utils.http import url_has_allowed_host_and_scheme

    allowed_hosts = {request.get_host()}
    if not url_has_allowed_host_and_scheme(
        url=url,
        allowed_hosts=allowed_hosts,
        require_https=request.is_secure(),
    ):
        return None

    parsed = urlsplit(url)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    if parsed.fragment:
        path += "#" + parsed.fragment

    if not path.startswith("/") or path.startswith("//"):
        return None
    return path


def redirect_back(request, default):
    """
    Safely redirect back to the referer or a 'next' parameter,
    falling back to the provided default if neither is safe or present.
    """
    from django.shortcuts import redirect

    # Priority 1: 'next' parameter in POST or GET
    next_url = request.POST.get("next") or request.GET.get("next")

    # Priority 2: HTTP_REFERER
    referer = request.META.get("HTTP_REFERER")

    for url in [next_url, referer]:
        safe_url = get_safe_url(request, url)
        if safe_url:
            return redirect(safe_url)

    return redirect(default)


# ---------------------------------------------------------------------------
# Balance calculation and Discount utilities
# ---------------------------------------------------------------------------


def compute_sliding_discount_rounded(total_fees: Decimal, percent: Decimal) -> Decimal:
    """Compute sliding-scale discount as a positive Decimal rounded to the nearest dollar.

    The discount is percent of total_fees, then rounded to whole dollars using half-down rounding
    (exactly .50 rounds down; above .50 rounds up; below .50 rounds down). If inputs are missing, returns 0.
    """
    if total_fees is None or percent is None:
        return Decimal("0")
    try:
        amount = (total_fees * percent) / Decimal("100")
    except Exception:
        return Decimal("0")
    # Round to the nearest whole dollar (e.g., 12.49 -> 12, 12.50 -> 12)
    return amount.quantize(Decimal("1."), rounding=ROUND_HALF_DOWN)


def get_student_balance_data(student, program, can_view_sliding=True):
    """
    Computes entries, total fees, sliding discount, total payments, and balance for
    a student in a specific program. Matches the logic used in views.
    """
    from .models import Fee, Payment, SlidingScale

    # Gather entries: fees (program), sliding scale (if exists), and payments
    entries = []
    sliding = SlidingScale.objects.filter(student=student, program=program).first()

    # Fees: positive amounts
    fees = Fee.objects.filter(program=program)
    for fee in fees:
        if (
            fee.assignments.exists()
            and not fee.assignments.filter(student=student).exists()
        ):
            continue
        fee_date = fee.date or (fee.created_at.date() if fee.created_at else None)
        adjusted_amount = fee.amount
        if sliding and sliding.percent is not None and can_view_sliding:
            if not sliding.date or (fee_date and fee_date >= sliding.date):
                discount = compute_sliding_discount_rounded(fee.amount, sliding.percent)
                adjusted_amount = fee.amount - discount

        entries.append(
            {
                "date": fee_date,
                "type": "Fee",
                "name": fee.name,
                "amount": fee.amount,
                "adjusted_amount": adjusted_amount,
            }
        )

    # Compute total fees for discount: ONLY include fees applicable to this student
    # and on or after the sliding scale's effective date.
    applicable_fees_for_discount = []
    for fee in Fee.objects.filter(program=program):
        if (
            fee.assignments.exists()
            and not fee.assignments.filter(student=student).exists()
        ):
            continue

        fee_date = fee.date or (fee.created_at.date() if fee.created_at else None)
        if sliding and sliding.date and fee_date and fee_date < sliding.date:
            continue

        applicable_fees_for_discount.append(fee.amount)

    total_fees_for_discount = sum(
        applicable_fees_for_discount,
        start=Decimal("0"),
    )
    if sliding and sliding.percent is not None and can_view_sliding:
        discount = compute_sliding_discount_rounded(
            total_fees_for_discount, sliding.percent
        )
        entries.append(
            {
                "date": sliding.date or sliding.created_at.date(),
                "type": "Sliding Scale",
                "name": f"Sliding scale (owes {sliding.percent}%)",
                "amount": Decimal("0.00"),
                "adjusted_amount": Decimal("0.00"),
            }
        )
    else:
        discount = Decimal("0")

    # Payments: negative amounts
    payments = Payment.objects.filter(student=student, program=program)
    for p in payments:
        via = dict(Payment.PAID_VIA_CHOICES).get(p.paid_via, p.paid_via)
        details = (
            f" (check #{p.check_number})"
            if (p.paid_via == "check" and p.check_number)
            else ""
        )
        if p.paid_via == "other" and p.notes:
            details += f" — {p.notes}"
        entries.append(
            {
                "date": p.paid_on,
                "type": "Payment",
                "name": f"Payment via {via}{details}",
                "amount": -p.amount,
                "adjusted_amount": -p.amount,
                "payment_id": p.id,
            }
        )

    # Sort by date
    entries.sort(key=lambda e: (e["date"] is None, e["date"], e["type"]))

    total_fees = sum([e["amount"] for e in entries if e["type"] == "Fee"])
    total_sliding = discount
    total_payments = -sum(
        [e["amount"] for e in entries if e["type"] == "Payment"]
    )  # positive figure
    balance = total_fees - total_sliding - total_payments

    return {
        "entries": entries,
        "total_fees": total_fees,
        "total_sliding": total_sliding,
        "total_payments": total_payments,
        "balance": balance,
        "sliding_scale": sliding,
    }
