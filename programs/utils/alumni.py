"""Alumni conversion: find and create Adult records from Students."""

from __future__ import annotations

from ..models import Adult


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
    # 1. Direct link
    if student.pk:
        a = Adult.objects.filter(student_record=student).first()
        if a:
            return a

    first = (
        getattr(student, "preferred_first_name", None)
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
                    legal_first_name__iexact=first,
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
            legal_first_name__iexact=first, last_name__iexact=last, is_alumni=True
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
    adult = find_matching_alumni_adult(student)
    created = False
    if adult is None:
        adult = Adult.objects.create(
            legal_first_name=student.legal_first_name or "",
            preferred_first_name=student.preferred_first_name,
            last_name=student.last_name or "",
            pronouns=student.pronouns,
            address=student.address,
            city=student.city,
            state=student.state,
            zip_code=student.zip_code,
            phone_number=student.phone_number,
            phone_type=student.phone_type,
            can_receive_texts=student.can_receive_texts,
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
            "preferred_first_name": "preferred_first_name",
            "pronouns": "pronouns",
            "address": "address",
            "city": "city",
            "state": "state",
            "zip_code": "zip_code",
            "phone_number": "phone_number",
            "phone_type": "phone_type",
            "can_receive_texts": "can_receive_texts",
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
