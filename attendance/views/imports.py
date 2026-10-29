import csv
import io
from datetime import timezone as dt_timezone

from django.contrib import messages
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive, make_aware
from django.views import View

from programs.models import Program, Student
from programs.permission_views import can_user_write
from programs.utils import redirect_back

from ..models import AttendanceEvent, AttendanceSession


class AttendanceImportView(View):
    def post(self, request):
        if not can_user_write(request.user, "attendance"):
            messages.error(request, "You do not have permission to import attendance.")
            return redirect_back(request, "import_dashboard")
        file = request.FILES.get("file")
        program_id = request.POST.get("program_id")
        overwrite = request.POST.get("overwrite") == "1"
        if not program_id:
            messages.error(request, "Please select a program for this import.")
            return redirect_back(request, "import_dashboard")
        program = Program.objects.filter(id=program_id).first()
        if not program:
            messages.error(request, "Selected program was not found.")
            return redirect_back(request, "import_dashboard")
        if not program.has_feature("attendance"):
            messages.error(
                request, "Attendance is not enabled for the selected program."
            )
            return redirect_back(request, "import_dashboard")
        if not file:
            messages.error(request, "No file uploaded.")
            return redirect_back(request, "import_dashboard")

        name = file.name.lower()
        if not name.endswith(".csv"):
            messages.error(request, "Unsupported file type. Please upload a CSV file.")
            return redirect_back(request, "import_dashboard")

        created = 0
        updated = 0
        errors = 0
        skipped = 0
        text = io.TextIOWrapper(file.file, encoding="utf-8")
        reader = csv.DictReader(text)

        utc = dt_timezone.utc

        def parse_utc(dt_val):
            if not dt_val:
                return None
            if hasattr(dt_val, "tzinfo"):
                # Already a datetime
                dt = dt_val
            else:
                dt = parse_datetime(str(dt_val).strip())
            if not dt:
                return None
            if is_naive(dt):
                # Treat naive as local time per system settings
                return make_aware(
                    dt, timezone=timezone.get_current_timezone()
                ).astimezone(utc)
            # Ensure in UTC for storage consistency
            return dt.astimezone(utc)

        def parse_team_number(row):
            raw = (
                row.get("visitor_team_number")
                or row.get("team_number")
                or row.get("team")
                or row.get("Visitor Team Number")
                or row.get("Team Number")
                or ""
            )
            value = str(raw).strip()
            if not value:
                return None
            try:
                team_number = int(value)
            except (TypeError, ValueError):
                return None
            return team_number if team_number > 0 else None

        def find_student(first_name, last_name, rfid):
            # Priority: RFID match
            if rfid:
                from attendance.services import resolve_card_by_uid

                card = resolve_card_by_uid(str(rfid).strip())
                if card and card.student:
                    return card.student
            # Next: name match (case-insensitive)
            fn = (first_name or "").strip()
            ln = (last_name or "").strip()
            if fn and ln:
                student = Student.objects.filter(
                    preferred_first_name__iexact=fn, last_name__iexact=ln
                ).first()
                if student:
                    return student
                student = Student.objects.filter(
                    legal_first_name__iexact=fn, last_name__iexact=ln
                ).first()
                if student:
                    return student
            return None

        try:
            for row in reader:
                first = (row.get("first_name") or row.get("First Name") or "").strip()
                last = (row.get("last_name") or row.get("Last Name") or "").strip()
                rfid = (
                    row.get("rfid")
                    or row.get("rfid_uid")
                    or row.get("RFID")
                    or row.get("RFID UID")
                    or ""
                ).strip()
                t_in_raw = (
                    row.get("time_in")
                    or row.get("time_in_utc")
                    or row.get("Time In (UTC)")
                    or row.get("time in (utc)")
                    or row.get("time_in (utc)")
                    or row.get("Time In")
                )
                t_out_raw = (
                    row.get("time_out")
                    or row.get("time_out_utc")
                    or row.get("Time Out (UTC)")
                    or row.get("time out (utc)")
                    or row.get("time_out (utc)")
                    or row.get("Time Out")
                )

                check_in = parse_utc(t_in_raw)
                check_out = parse_utc(t_out_raw)
                visitor_team_number = parse_team_number(row)
                if not check_in:
                    errors += 1
                    continue

                student = find_student(first, last, rfid)
                visitor_name = ""
                if not student:
                    # If we cannot find a student, record as visitor session with provided name or RFID
                    if first or last:
                        visitor_name = (first + " " + last).strip()
                    elif rfid:
                        visitor_name = f"RFID {rfid}"
                    else:
                        visitor_name = "Unknown"

                # Idempotency: try to find existing session with same keys
                if student:
                    existing = AttendanceSession.objects.filter(
                        program=program, student=student, check_in=check_in
                    ).first()
                else:
                    existing = AttendanceSession.objects.filter(
                        program=program,
                        student__isnull=True,
                        visitor_name=visitor_name,
                        check_in=check_in,
                    ).first()

                if existing:
                    # Update checkout if new info is provided
                    fields_to_update = []
                    if (
                        overwrite
                        and check_out
                        and (not existing.check_out or existing.check_out != check_out)
                    ):
                        existing.check_out = check_out
                        existing.recompute_duration()
                        fields_to_update.extend(
                            [
                                "check_out",
                                "duration_minutes",
                                "updated_at",
                            ]
                        )

                    if (
                        overwrite
                        and not student
                        and visitor_team_number
                        and existing.visitor_team_number != visitor_team_number
                    ):
                        existing.visitor_team_number = visitor_team_number
                        fields_to_update.append("visitor_team_number")

                    if fields_to_update:
                        existing.save(update_fields=fields_to_update)
                        updated += 1
                    else:
                        skipped += 1
                    continue

                # Create linked events (optional)
                open_event = AttendanceEvent.objects.create(
                    program=program,
                    student=student,
                    visitor_name=visitor_name if not student else "",
                    visitor_team_number=visitor_team_number if not student else None,
                    rfid_uid=rfid or "",
                    kiosk=None,
                    event_type=AttendanceEvent.IN,
                    occurred_at=check_in,
                    source="import",
                    notes="Imported from CSV",
                )
                close_event = None
                if check_out:
                    close_event = AttendanceEvent.objects.create(
                        program=program,
                        student=student,
                        visitor_name=visitor_name if not student else "",
                        visitor_team_number=(
                            visitor_team_number if not student else None
                        ),
                        rfid_uid=rfid or "",
                        kiosk=None,
                        event_type=AttendanceEvent.OUT,
                        occurred_at=check_out,
                        source="import",
                        notes="Imported from CSV",
                    )

                session = AttendanceSession(
                    program=program,
                    student=student,
                    visitor_name=visitor_name if not student else "",
                    visitor_team_number=visitor_team_number if not student else None,
                    check_in=check_in,
                    check_out=check_out,
                    opened_by_event=open_event,
                    closed_by_event=close_event,
                )
                session.recompute_duration()
                session.save()
                created += 1

            if errors:
                messages.warning(
                    request,
                    f"Attendance import completed: {created} created, {updated} updated, "
                    f"{skipped} skipped, {errors} rows had errors.",
                )
            else:
                messages.success(
                    request,
                    f"Attendance import completed: {created} created, {updated} updated, {skipped} skipped.",
                )
        except Exception as e:
            messages.error(request, f"Failed to import attendance: {e}")

        return redirect_back(request, "import_dashboard")
