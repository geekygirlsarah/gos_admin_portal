from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import ListView, DetailView, CreateView, UpdateView, View

from django.core.mail import EmailMultiAlternatives, get_connection
from django.utils.html import strip_tags
from django.conf import settings
from django.db.models.functions import Coalesce, Lower

from .models import Program, Student, Enrollment, Parent, Mentor, Payment, SlidingScale, Fee, School, Alumni, StudentApplication, RELATIONSHIP_CHOICES
from .forms import (
    StudentForm,
    AddExistingStudentToProgramForm,
    QuickCreateStudentForm,
    ParentForm,
    PaymentForm,
    SlidingScaleForm,
    SchoolForm,
    MentorForm,
    ProgramForm,
    ProgramEmailForm,
    FeeAssignmentEditForm,
    FeeForm,
    ProgramApplySelectForm,
    StudentApplicationForm,
)


class ProgramListView(ListView):
    model = Program
    template_name = 'home.html'  # landing page
    context_object_name = 'programs'

    def get_queryset(self):
        # Keep a base queryset; ordering will be handled in context via grouping
        return Program.objects.all()

    def get_context_data(self, **kwargs):
        from django.utils import timezone
        from operator import attrgetter
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        programs = list(ctx['programs'])

        def status(prog):
            sd = prog.start_date
            ed = prog.end_date
            if sd and sd > today:
                return 'future'
            if ed and ed < today:
                return 'past'
            # If only start or only end or none: treat as current if not clearly future/past
            return 'current'

        def sort_key(prog):
            # Sort by start_date (None last), then by name
            sd = prog.start_date
            # Use a tuple where None sorts after real dates
            return (sd is None, sd or today, prog.name or '')

        future = sorted([p for p in programs if status(p) == 'future'], key=sort_key)
        current = sorted([p for p in programs if status(p) == 'current'], key=sort_key)
        past = sorted([p for p in programs if status(p) == 'past'], key=sort_key)

        ctx.update({
            'future_programs': future,
            'current_programs': current,
            'past_programs': past,
        })
        return ctx


class StudentListView(LoginRequiredMixin, ListView):
    model = Student
    template_name = 'students/list.html'
    context_object_name = 'students'

    def get_queryset(self):
        qs = super().get_queryset()
        # Order by preferred/display name if present, otherwise legal first name, then last name (case-insensitive)
        return qs.annotate(
            sort_first=Coalesce('first_name', 'legal_first_name'),
        ).order_by(Lower('sort_first'), Lower('last_name'))


class StudentPhotoListView(LoginRequiredMixin, ListView):
    model = Student
    template_name = 'students/photo_grid.html'
    context_object_name = 'students'
    paginate_by = 48

    def get_queryset(self):
        qs = super().get_queryset()
        # Order by preferred/display name if present, otherwise legal first name, then last name (case-insensitive)
        return qs.annotate(
            sort_first=Coalesce('first_name', 'legal_first_name'),
        ).order_by(Lower('sort_first'), Lower('last_name'))


class ProgramStudentPhotoListView(LoginRequiredMixin, ListView):
    model = Student
    template_name = 'students/photo_grid.html'
    context_object_name = 'students'
    paginate_by = 48

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs.get('pk'))
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        # Students enrolled in this program
        qs = Student.objects.filter(programs=self.program)
        return qs.annotate(
            sort_first=Coalesce('first_name', 'legal_first_name'),
        ).order_by(Lower('sort_first'), Lower('last_name'))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['program'] = self.program
        return ctx


class StudentEmergencyContactsView(LoginRequiredMixin, ListView):
    model = Student
    template_name = 'students/emergency_contacts.html'
    context_object_name = 'students'

    def get_queryset(self):
        qs = super().get_queryset().filter(active=True)
        return qs.select_related('school', 'primary_contact', 'secondary_contact').prefetch_related('parents').annotate(
            sort_first=Coalesce('first_name', 'legal_first_name'),
        ).order_by(Lower('sort_first'), Lower('last_name'))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Backwards compatibility: some templates expect 'plist'
        ctx.setdefault('plist', ctx.get('students') or ctx.get('object_list'))
        return ctx


class StudentsByGradeView(LoginRequiredMixin, ListView):
    model = Student
    template_name = 'students/by_grade.html'
    context_object_name = 'students'

    def get_queryset(self):
        qs = super().get_queryset().filter(active=True)
        return qs.select_related('school').annotate(
            sort_first=Coalesce('first_name', 'legal_first_name'),
        ).order_by('graduation_year', Lower('last_name'), Lower('sort_first'))

    def get_context_data(self, **kwargs):
        from django.utils import timezone
        ctx = super().get_context_data(**kwargs)
        current_year = timezone.now().year
        grouped = {
            '12th Grade': [],
            '11th Grade': [],
            '10th Grade': [],
            '9th Grade': [],
            'Unknown Grade': [],
        }
        for s in ctx['students']:
            gy = s.graduation_year
            grade_label = 'Unknown Grade'
            if gy:
                # Approximate US grade level: grad this year = 12th
                delta = gy - current_year
                grade_num = 12 - delta
                if 9 <= grade_num <= 12:
                    grade_label = f"{grade_num}th Grade"
            grouped.setdefault(grade_label, [])
            grouped[grade_label].append(s)
        # Order groups by typical 12->9, then unknown
        order = ['12th Grade', '11th Grade', '10th Grade', '9th Grade', 'Unknown Grade']
        ctx['grouped'] = [(label, grouped.get(label, [])) for label in order]
        return ctx


class StudentsBySchoolView(LoginRequiredMixin, ListView):
    model = Student
    template_name = 'students/by_school.html'
    context_object_name = 'students'

    def get_queryset(self):
        qs = super().get_queryset().filter(active=True)
        return qs.select_related('school').annotate(
            sort_first=Coalesce('first_name', 'legal_first_name'),
        ).order_by('school__name', Lower('last_name'), Lower('sort_first'))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        grouped = {}
        for s in ctx['students']:
            label = s.school.name if s.school_id else 'No School'
            grouped.setdefault(label, []).append(s)
        # Sort by school label
        ctx['grouped'] = sorted(grouped.items(), key=lambda kv: (kv[0] == 'No School', kv[0]))
        return ctx


class ParentListView(LoginRequiredMixin, ListView):
    model = Parent
    template_name = 'parents/list.html'
    context_object_name = 'parents'

    def get_queryset(self):
        # Prefetch related students to avoid N+1 queries and order by name
        return Parent.objects.all().prefetch_related('students').order_by('first_name', 'last_name')


class MentorListView(LoginRequiredMixin, ListView):
    model = Mentor
    template_name = 'mentors/list.html'
    context_object_name = 'mentors'


class AlumniListView(LoginRequiredMixin, ListView):
    model = Alumni
    template_name = 'alumni/list.html'
    context_object_name = 'alumni'

    def get_queryset(self):
        # Include related student to avoid N+1 and sort by student name
        return Alumni.objects.select_related('student').order_by('student__last_name', 'student__first_name')


class StudentConvertToAlumniView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'programs.change_student'

    def post(self, request, pk):
        from django.contrib import messages
        from django.shortcuts import redirect, get_object_or_404
        student = get_object_or_404(Student, pk=pk)
        alumni, created = Alumni.objects.get_or_create(student=student)
        # Deactivate student as part of conversion convention
        if student.active:
            student.active = False
            student.save(update_fields=['active', 'updated_at'])
        if created:
            messages.success(request, f"Converted {student} to Alumni and deactivated the student.")
        else:
            messages.info(request, f"{student} already has an Alumni record. Student has been deactivated.")
        # Redirect back to list or provided next
        next_url = request.GET.get('next') or request.POST.get('next')
        return redirect(next_url or 'student_list')


class StudentBulkConvertToAlumniView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'programs.change_student'
    template_name = 'students/convert_to_alumni.html'

    def get(self, request):
        from django.utils import timezone
        from django.shortcuts import render
        year = request.GET.get('year')
        try:
            year = int(year) if year else timezone.now().year
        except ValueError:
            year = timezone.now().year
        # Default to seniors: graduation_year equals the selected year, and active
        students = Student.objects.filter(graduation_year=year, active=True).order_by('last_name', 'first_name')
        return render(request, self.template_name, {
            'year': year,
            'students': students,
        })

    def post(self, request):
        from django.shortcuts import redirect, render
        action = request.POST.get('action', 'convert')
        ids = request.POST.getlist('student_ids')
        year = request.POST.get('year')
        if not ids:
            messages.info(request, 'No students selected.')
            if year:
                return redirect(f"{reverse('student_bulk_convert_select')}?year={year}")
            return redirect('student_bulk_convert_select')

        qs = Student.objects.filter(pk__in=ids).order_by('last_name', 'first_name')

        if action == 'preview':
            # Build preview info without writing changes
            existing_alumni_ids = set(Alumni.objects.filter(student_id__in=ids).values_list('student_id', flat=True))
            will_create = [s for s in qs if s.pk not in existing_alumni_ids]
            already_alumni = [s for s in qs if s.pk in existing_alumni_ids]
            will_deactivate = [s for s in qs if s.active]
            return render(request, 'students/convert_to_alumni_preview.html', {
                'year': year,
                'students': qs,
                'will_create': will_create,
                'already_alumni': already_alumni,
                'will_deactivate': will_deactivate,
                'ids': ids,
            })

        # Default: perform conversion
        created = 0
        existed = 0
        deactivated = 0
        for student in qs:
            alumni, was_created = Alumni.objects.get_or_create(student=student)
            if was_created:
                created += 1
            else:
                existed += 1
            if student.active:
                student.active = False
                student.save(update_fields=['active', 'updated_at'])
                deactivated += 1
        messages.success(request, f"Converted {created} new alumni, {existed} already existed. Deactivated {deactivated} student(s).")
        return redirect('alumni_list')


class ImportDashboardView(LoginRequiredMixin, View):
    def get(self, request):
        from django.shortcuts import render
        return render(request, 'imports/dashboard.html')


class StudentImportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'programs.add_student'
    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            messages.error(request, 'No file uploaded.')
            return redirect('import_dashboard')
        name = file.name.lower()
        created = 0
        updated = 0
        errors = 0
        try:
            if name.endswith('.csv'):
                import csv, io
                text = io.TextIOWrapper(file.file, encoding='utf-8')
                reader = csv.DictReader(text)
                rows = list(reader)
            elif name.endswith('.xlsx'):
                from openpyxl import load_workbook
                wb = load_workbook(filename=file, read_only=True, data_only=True)
                ws = wb.active
                headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
                rows = []
                for r in ws.iter_rows(min_row=2, values_only=True):
                    rows.append({headers[i]: r[i] for i in range(len(headers))})
            else:
                messages.error(request, 'Unsupported file type. Please upload CSV or XLSX.')
                return redirect('import_dashboard')

            # Helpers
            from datetime import datetime, date

            def raw(d, *keys):
                for k in keys:
                    if k in d and d[k] is not None:
                        return d[k]
                return None

            def val(d, *keys):
                for k in keys:
                    if k in d and d[k] is not None:
                        v = str(d[k]).strip()
                        if v != '' and v.lower() != 'none':
                            return v
                return None

            def val_bool(d, *keys):
                v = val(d, *keys)
                if v is None:
                    return None
                s = v.strip().lower()
                if s in ('y', 'yes', 'true', 't', '1'): return True
                if s in ('n', 'no', 'false', 'f', '0'): return False
                return None

            def val_date(d, *keys):
                # Accept date objects from XLSX or parse common string formats
                rv = raw(d, *keys)
                if isinstance(rv, datetime):
                    return rv.date()
                if isinstance(rv, date):
                    return rv
                v = val(d, *keys)
                if not v:
                    return None
                for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y'):
                    try:
                        return datetime.strptime(v, fmt).date()
                    except Exception:
                        pass
                return None

            def get_or_create_parent(first, last, email):
                # Try to find by email first
                if email:
                    p = Parent.objects.filter(email__iexact=email).first()
                    if p:
                        changed_parent = False
                        if first and p.first_name != first:
                            p.first_name = first
                            changed_parent = True
                        if last and p.last_name != last:
                            p.last_name = last
                            changed_parent = True
                        if changed_parent:
                            p.save()
                        return p
                # Next try by name match
                if first and last:
                    p = Parent.objects.filter(first_name__iexact=first, last_name__iexact=last).first()
                    if p:
                        if email and (p.email or '').lower() != (email or '').lower():
                            p.email = email
                            p.save()
                        return p
                # If we have at least one of name or email, create
                if first or last or email:
                    return Parent.objects.create(
                        first_name=first or (email.split('@')[0] if email else 'Parent'),
                        last_name=last or '(contact)',
                        email=email or None,
                    )
                return None

            for d in rows:
                first = val(d, 'first_name', 'First Name', 'Preferred First Name')
                legal_first = val(d, 'legal_first_name', 'Legal First Name') or first
                last = val(d, 'last_name', 'Last Name')
                if not last or not legal_first:
                    errors += 1
                    continue

                # Simple strings
                pronouns = val(d, 'pronouns', 'Pronouns')
                address = val(d, 'address', 'Address', 'Street Address')
                city = val(d, 'city', 'City')
                state = val(d, 'state', 'State')
                zip_code = val(d, 'zip_code', 'Zip Code', 'ZIP', 'Zip')
                cell_phone = val(d, 'cell_phone_number', 'Cell Phone Number', 'Cell Phone', 'Phone', 'Phone Number')
                personal_email = val(d, 'personal_email', 'Email', 'Personal Email')
                andrew_id = val(d, 'andrew_id', 'Andrew ID', 'AndrewID')
                andrew_email = val(d, 'andrew_email', 'Andrew Email')
                race_ethnicity = val(d, 'race_ethnicity', 'Race/Ethnicity', 'Race', 'Ethnicity')
                tshirt_size = val(d, 'tshirt_size', 'T-Shirt Size', 'Shirt Size')
                discord_handle = val(d, 'discord_handle', 'Discord Handle', 'Discord', 'Discord Username')

                # Dates and booleans
                dob = val_date(d, 'date_of_birth', 'Date of Birth', 'DOB', 'Birthdate')
                seen_once = val_bool(d, 'seen_once', 'Seen Once')
                on_discord = val_bool(d, 'on_discord', 'On Discord')
                active = val_bool(d, 'active', 'Active')

                # School/year
                school_name = val(d, 'school', 'School')
                grad = val(d, 'graduation_year', 'Graduation Year')
                school = None
                if school_name:
                    school, _ = School.objects.get_or_create(name=school_name)
                grad_year = None
                if grad and str(grad).isdigit():
                    grad_year = int(str(grad))

                obj, created_flag = Student.objects.get_or_create(
                    last_name=last,
                    legal_first_name=legal_first,
                    defaults={
                        'first_name': first if first != legal_first else None,
                        'pronouns': pronouns,
                        'date_of_birth': dob,
                        'address': address,
                        'city': city,
                        'state': state,
                        'zip_code': zip_code,
                        'cell_phone_number': cell_phone,
                        'personal_email': personal_email,
                        'andrew_id': andrew_id,
                        'andrew_email': andrew_email,
                        'race_ethnicity': race_ethnicity,
                        'tshirt_size': tshirt_size,
                        'seen_once': seen_once if seen_once is not None else False,
                        'on_discord': on_discord if on_discord is not None else False,
                        'discord_handle': discord_handle,
                        'school': school,
                        'graduation_year': grad_year,
                        'active': active if active is not None else True,
                    }
                )
                if created_flag:
                    created += 1
                else:
                    changed = False
                    # Strings and relations
                    for field, value in [
                        ('first_name', first),
                        ('pronouns', pronouns),
                        ('address', address),
                        ('city', city),
                        ('state', state),
                        ('zip_code', zip_code),
                        ('cell_phone_number', cell_phone),
                        ('personal_email', personal_email),
                        ('andrew_id', andrew_id),
                        ('andrew_email', andrew_email),
                        ('race_ethnicity', race_ethnicity),
                        ('tshirt_size', tshirt_size),
                        ('discord_handle', discord_handle),
                    ]:
                        if value and getattr(obj, field) != value:
                            setattr(obj, field, value)
                            changed = True
                    if dob and obj.date_of_birth != dob:
                        obj.date_of_birth = dob
                        changed = True
                    if school and obj.school != school:
                        obj.school = school
                        changed = True
                    if grad_year and obj.graduation_year != grad_year:
                        obj.graduation_year = grad_year
                        changed = True
                    # Booleans (allow False updates)
                    if seen_once is not None and obj.seen_once != seen_once:
                        obj.seen_once = seen_once
                        changed = True
                    if on_discord is not None and obj.on_discord != on_discord:
                        obj.on_discord = on_discord
                        changed = True
                    if active is not None and obj.active != active:
                        obj.active = active
                        changed = True
                    if changed:
                        obj.save()
                        updated += 1

                # Parent linkage (primary and secondary)
                prim_first = val(d, 'primary_parent_first_name', 'Primary Parent First Name', 'Primary First Name', 'Primary First')
                prim_last = val(d, 'primary_parent_last_name', 'Primary Parent Last Name', 'Primary Last Name', 'Primary Last')
                prim_email = val(d, 'primary_parent_email', 'Primary Parent Email', 'Primary Email', 'Primary E-mail', 'Primary Email Address')
                sec_first = val(d, 'secondary_parent_first_name', 'Secondary Parent First Name', 'Secondary First Name', 'Secondary First')
                sec_last = val(d, 'secondary_parent_last_name', 'Secondary Parent Last Name', 'Secondary Last Name', 'Secondary Last')
                sec_email = val(d, 'secondary_parent_email', 'Secondary Parent Email', 'Secondary Email', 'Secondary E-mail', 'Secondary Email Address')

                contact_changed = False
                primary = get_or_create_parent(prim_first, prim_last, prim_email)
                secondary = get_or_create_parent(sec_first, sec_last, sec_email)
                if primary:
                    if obj.primary_contact_id != getattr(primary, 'id', None):
                        obj.primary_contact = primary
                        contact_changed = True
                    # Ensure M2M link exists
                    if primary.id and not obj.parents.filter(id=primary.id).exists():
                        obj.parents.add(primary)
                if secondary:
                    if obj.secondary_contact_id != getattr(secondary, 'id', None):
                        obj.secondary_contact = secondary
                        contact_changed = True
                    if secondary.id and not obj.parents.filter(id=secondary.id).exists():
                        obj.parents.add(secondary)
                if contact_changed:
                    obj.save(update_fields=['primary_contact', 'secondary_contact', 'updated_at'])
                    if not created_flag:
                        # Only count as updated when not newly created and not already counted
                        updated += 1
            if created or updated:
                messages.success(request, f'Imported {created} new, updated {updated}. Skipped {errors}.')
            else:
                messages.info(request, 'No rows imported.')
        except Exception as e:
            messages.error(request, f'Import failed: {e}')
        return redirect('import_dashboard')


class ParentImportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'programs.add_parent'
    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            messages.error(request, 'No file uploaded.')
            return redirect('import_dashboard')
        name = file.name.lower()
        created = 0
        updated = 0
        errors = 0
        try:
            if name.endswith('.csv'):
                import csv, io
                text = io.TextIOWrapper(file.file, encoding='utf-8')
                reader = csv.DictReader(text)
                rows = list(reader)
            elif name.endswith('.xlsx'):
                from openpyxl import load_workbook
                wb = load_workbook(filename=file, read_only=True)
                ws = wb.active
                headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
                rows = []
                for r in ws.iter_rows(min_row=2, values_only=True):
                    rows.append({headers[i]: r[i] for i in range(len(headers))})
            else:
                messages.error(request, 'Unsupported file type. Please upload CSV or XLSX.')
                return redirect('import_dashboard')

            def val(d, *keys):
                for k in keys:
                    if k in d and d[k] is not None:
                        v = str(d[k]).strip()
                        if v != '' and v.lower() != 'none':
                            return v
                return None

            for d in rows:
                first = val(d, 'first_name', 'First Name')
                last = val(d, 'last_name', 'Last Name')
                if not first or not last:
                    errors += 1
                    continue
                email = val(d, 'email', 'Email')
                phone = val(d, 'phone_number', 'Phone', 'Phone Number')
                obj, created_flag = Parent.objects.get_or_create(
                    first_name=first, last_name=last,
                    defaults={'email': email, 'phone_number': phone}
                )
                if created_flag:
                    created += 1
                else:
                    changed = False
                    if email and obj.email != email:
                        obj.email = email
                        changed = True
                    if phone and obj.phone_number != phone:
                        obj.phone_number = phone
                        changed = True
                    if changed:
                        obj.save()
                        updated += 1
            messages.success(request, f'Imported {created} new, updated {updated}. Skipped {errors}.')
        except Exception as e:
            messages.error(request, f'Import failed: {e}')
        return redirect('import_dashboard')


class MentorImportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'programs.add_mentor'
    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            messages.error(request, 'No file uploaded.')
            return redirect('import_dashboard')
        name = file.name.lower()
        created = 0
        updated = 0
        errors = 0
        try:
            if name.endswith('.csv'):
                import csv, io
                text = io.TextIOWrapper(file.file, encoding='utf-8')
                reader = csv.DictReader(text)
                rows = list(reader)
            elif name.endswith('.xlsx'):
                from openpyxl import load_workbook
                wb = load_workbook(filename=file, read_only=True)
                ws = wb.active
                headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
                rows = []
                for r in ws.iter_rows(min_row=2, values_only=True):
                    rows.append({headers[i]: r[i] for i in range(len(headers))})
            else:
                messages.error(request, 'Unsupported file type. Please upload CSV or XLSX.')
                return redirect('import_dashboard')

            def val(d, *keys):
                for k in keys:
                    if k in d and d[k] is not None:
                        v = str(d[k]).strip()
                        if v != '' and v.lower() != 'none':
                            return v
                return None

            for d in rows:
                first = val(d, 'first_name', 'First Name')
                last = val(d, 'last_name', 'Last Name')
                if not first or not last:
                    errors += 1
                    continue
                email = val(d, 'personal_email', 'Email', 'Personal Email')
                andrew_email = val(d, 'andrew_email', 'Andrew Email')
                role = val(d, 'role', 'Role') or 'mentor'
                obj, created_flag = Mentor.objects.get_or_create(
                    first_name=first, last_name=last,
                    defaults={'personal_email': email, 'andrew_email': andrew_email, 'role': role}
                )
                if created_flag:
                    created += 1
                else:
                    changed = False
                    for field, value in [('personal_email', email), ('andrew_email', andrew_email), ('role', role)]:
                        if value and getattr(obj, field) != value:
                            setattr(obj, field, value)
                            changed = True
                    if changed:
                        obj.save()
                        updated += 1
            messages.success(request, f'Imported {created} new, updated {updated}. Skipped {errors}.')
        except Exception as e:
            messages.error(request, f'Import failed: {e}')
        return redirect('import_dashboard')


class SchoolImportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'programs.add_school'
    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            messages.error(request, 'No file uploaded.')
            return redirect('import_dashboard')
        name = file.name.lower()
        created = 0
        updated = 0
        errors = 0
        try:
            if name.endswith('.csv'):
                import csv, io
                text = io.TextIOWrapper(file.file, encoding='utf-8')
                reader = csv.DictReader(text)
                rows = list(reader)
            elif name.endswith('.xlsx'):
                from openpyxl import load_workbook
                wb = load_workbook(filename=file, read_only=True)
                ws = wb.active
                headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
                rows = []
                for r in ws.iter_rows(min_row=2, values_only=True):
                    rows.append({headers[i]: r[i] for i in range(len(headers))})
            else:
                messages.error(request, 'Unsupported file type. Please upload CSV or XLSX.')
                return redirect('import_dashboard')

            def val(d, *keys):
                for k in keys:
                    if k in d and d[k] is not None:
                        v = str(d[k]).strip()
                        if v != '' and v.lower() != 'none':
                            return v
                return None

            for d in rows:
                school_name = val(d, 'name', 'Name', 'School')
                if not school_name:
                    errors += 1
                    continue
                district = val(d, 'district', 'District', 'School District')
                street = val(d, 'street_address', 'Street', 'Street Address', 'Address')
                city = val(d, 'city', 'City')
                state = val(d, 'state', 'State')
                zip_code = val(d, 'zip', 'ZIP', 'Zip', 'zip_code', 'Zip Code', 'Postal Code')
                obj, created_flag = School.objects.get_or_create(
                    name=school_name,
                    defaults={
                        'district': district,
                        'street_address': street,
                        'city': city,
                        'state': state,
                        'zip_code': zip_code,
                    }
                )
                if created_flag:
                    created += 1
                else:
                    changed = False
                    for field, value in [
                        ('district', district),
                        ('street_address', street),
                        ('city', city),
                        ('state', state),
                        ('zip_code', zip_code),
                    ]:
                        if value and getattr(obj, field) != value:
                            setattr(obj, field, value)
                            changed = True
                    if changed:
                        obj.save()
                        updated += 1
            messages.success(request, f'Imported {created} new, updated {updated}. Skipped {errors}.')
        except Exception as e:
            messages.error(request, f'Import failed: {e}')
        return redirect('import_dashboard')


class MentorCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Mentor
    form_class = MentorForm
    template_name = 'mentors/form.html'
    permission_required = 'programs.add_mentor'

    def get_success_url(self):
        return reverse('mentor_list')


class MentorUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Mentor
    form_class = MentorForm
    template_name = 'mentors/form.html'
    permission_required = 'programs.change_mentor'

    def get_success_url(self):
        next_url = self.request.GET.get('next')
        return next_url or reverse('mentor_edit', args=[self.object.pk])


# --- Schools list/create/edit ---
class SchoolListView(LoginRequiredMixin, ListView):
    model = School
    template_name = 'schools/list.html'
    context_object_name = 'schools'


class SchoolCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = School
    form_class = SchoolForm
    template_name = 'schools/form.html'
    permission_required = 'programs.add_school'

    def get_success_url(self):
        # After creating a School, return to the Schools listing
        return reverse('school_list')


class SchoolUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = School
    form_class = SchoolForm
    template_name = 'schools/form.html'
    permission_required = 'programs.change_school'

    def get_success_url(self):
        next_url = self.request.GET.get('next')
        return next_url or reverse('school_edit', args=[self.object.pk])


class ProgramEmailView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'programs.view_program'  # basic permission to access
    template_name = 'programs/email_form.html'

    def get(self, request, pk=None):
        program = get_object_or_404(Program, pk=pk) if pk else None
        form = ProgramEmailForm(program=program) if program else ProgramEmailForm()
        return self._render(form, program)

    def post(self, request, pk=None):
        program = get_object_or_404(Program, pk=pk) if pk else None
        form = ProgramEmailForm(request.POST, program=program) if program else ProgramEmailForm(request.POST)
        if form.is_valid():
            prog = program or form.cleaned_data['program']
            groups = form.cleaned_data['recipient_groups']
            subject = form.cleaned_data['subject']
            html_body = form.cleaned_data['body']
            text_body = strip_tags(html_body)
            test_email = form.cleaned_data.get('test_email')

            recipients = set()
            if 'students' in groups:
                for s in Student.objects.filter(programs=prog, active=True):
                    if s.personal_email:
                        recipients.add(s.personal_email)
                    elif s.andrew_email:
                        recipients.add(s.andrew_email)
            if 'parents' in groups:
                parent_emails = Parent.objects.filter(students__programs=prog, email_updates=True).values_list('email', flat=True)
                for e in parent_emails:
                    if e:
                        recipients.add(e)
            if 'mentors' in groups:
                # No explicit Program-Mentor link in models; fallback to all active mentors
                for m in Mentor.objects.filter(active=True):
                    if m.personal_email:
                        recipients.add(m.personal_email)
                    elif m.andrew_email:
                        recipients.add(m.andrew_email)

            if not recipients and not test_email:
                messages.error(request, 'No recipients found for the selected groups.')
                return self._render(form, prog)

            to_send = [test_email] if test_email else sorted(recipients)
            connection = get_connection(
                backend=getattr(settings, 'EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
            )
            from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@example.com')
            email = EmailMultiAlternatives(subject=subject, body=text_body, from_email=from_email, to=[])
            email.to = []  # ensure empty
            email.bcc = to_send
            email.attach_alternative(html_body, 'text/html')
            email.send(fail_silently=False)

            messages.success(request, f"Email sent to {len(to_send)} recipient(s){' (test only)' if test_email else ''}.")
            # Redirect back to program detail if coming from there, otherwise stay
            if pk:
                return redirect('program_detail', pk=pk)
            return redirect('program_messaging')

        return self._render(form, program)

    def _render(self, form, program):
        from django.shortcuts import render
        ctx = {'form': form, 'program': program}
        return render(self.request, self.template_name, ctx)


class ProgramDetailView(LoginRequiredMixin, DetailView):
    model = Program
    template_name = 'programs/detail.html'
    context_object_name = 'program'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        program = self.object
        # Prepare annotated queryset for consistent sorting
        from django.db.models.functions import Lower, Coalesce
        base_qs = program.students.select_related('user').all().annotate(
            sort_first=Lower(Coalesce('first_name', 'legal_first_name')),
            sort_last=Lower('last_name'),
        )
        # Split into active and inactive sections
        ctx['active_students'] = base_qs.filter(active=True).order_by('sort_first', 'sort_last')
        ctx['inactive_students'] = base_qs.filter(active=False).order_by('sort_first', 'sort_last')
        # Backwards compatibility (old templates may rely on a single list)
        ctx['enrolled_students'] = list(ctx['active_students']) + list(ctx['inactive_students'])
        can_manage = self.request.user.has_perm('programs.change_student') or self.request.user.has_perm('programs.add_student')
        ctx['can_manage_students'] = can_manage
        ctx['can_add_payment'] = self.request.user.has_perm('programs.add_payment')
        ctx['can_add_sliding_scale'] = self.request.user.has_perm('programs.add_slidingscale')
        ctx['can_manage_fees'] = self.request.user.has_perm('programs.change_fee')
        if can_manage:
            ctx['add_existing_form'] = AddExistingStudentToProgramForm(program=program)
            ctx['quick_create_form'] = QuickCreateStudentForm()
        return ctx


class ProgramCreateView(CreateView):
    model = Program
    form_class = ProgramForm
    template_name = 'programs/form.html'

    def get_success_url(self):
        return reverse('program_detail', args=[self.object.pk])


# --- Student edit ---
class StudentUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Student
    form_class = StudentForm
    template_name = 'students/form.html'
    permission_required = 'programs.change_student'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['RELATIONSHIP_CHOICES'] = RELATIONSHIP_CHOICES
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        # Persist relationship selections for each selected parent (note: global per Parent)
        rel_map = {k[len('parent_rel_'):]: v for k, v in self.request.POST.items() if k.startswith('parent_rel_')}
        valid_keys = set(k for k, _ in RELATIONSHIP_CHOICES)
        for pid_str, rel in rel_map.items():
            try:
                pid = int(pid_str)
            except (TypeError, ValueError):
                continue
            if rel in valid_keys:
                p = Parent.objects.filter(pk=pid).first()
                if p and p.relationship_to_student != rel:
                    p.relationship_to_student = rel
                    p.save(update_fields=['relationship_to_student'])
        return response

    def get_success_url(self):
        next_url = self.request.GET.get('next')
        return next_url or reverse('student_edit', args=[self.object.pk])


class StudentCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Student
    form_class = StudentForm
    template_name = 'students/form.html'
    permission_required = 'programs.add_student'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['RELATIONSHIP_CHOICES'] = RELATIONSHIP_CHOICES
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        # Persist relationship selections for each selected parent (note: global per Parent)
        rel_map = {k[len('parent_rel_'):]: v for k, v in self.request.POST.items() if k.startswith('parent_rel_')}
        valid_keys = set(k for k, _ in RELATIONSHIP_CHOICES)
        for pid_str, rel in rel_map.items():
            try:
                pid = int(pid_str)
            except (TypeError, ValueError):
                continue
            if rel in valid_keys:
                p = Parent.objects.filter(pk=pid).first()
                if p and p.relationship_to_student != rel:
                    p.relationship_to_student = rel
                    p.save(update_fields=['relationship_to_student'])
        return response

    def get_success_url(self):
        # After creating a Student, return to the Students listing
        return reverse('student_list')


class StudentDetailView(LoginRequiredMixin, DetailView):
    model = Student
    template_name = 'students/detail.html'
    context_object_name = 'student'


# --- Program student management actions ---
class ProgramStudentAddView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'programs.change_student'

    def post(self, request, pk):
        program = get_object_or_404(Program, pk=pk)
        form = AddExistingStudentToProgramForm(request.POST, program=program)
        if form.is_valid():
            student = form.cleaned_data['student']
            Enrollment.objects.get_or_create(student=student, program=program)
            messages.success(request, f'Added {student} to {program}.')
        else:
            messages.error(request, 'Could not add student to program.')
        return redirect('program_detail', pk=program.pk)


class ProgramStudentQuickCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'programs.add_student'

    def post(self, request, pk):
        program = get_object_or_404(Program, pk=pk)
        form = QuickCreateStudentForm(request.POST)
        if form.is_valid():
            student = form.save()
            Enrollment.objects.get_or_create(student=student, program=program)
            messages.success(request, f'Created {student} and added to {program}.')
        else:
            messages.error(request, 'Could not create student.')
        return redirect('program_detail', pk=program.pk)


class ProgramStudentRemoveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'programs.change_student'

    def post(self, request, pk, student_id):
        program = get_object_or_404(Program, pk=pk)
        student = get_object_or_404(Student, pk=student_id)
        Enrollment.objects.filter(student=student, program=program).delete()
        messages.success(request, f'Removed {student} from {program}.')
        return redirect('program_detail', pk=program.pk)


# --- Parent create/edit ---
class ParentCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Parent
    form_class = ParentForm
    template_name = 'parents/form.html'
    permission_required = 'programs.add_parent'

    def get_success_url(self):
        # After creating a Parent, return to the Parents listing
        return reverse('parent_list')


class ParentUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Parent
    form_class = ParentForm
    template_name = 'parents/form.html'
    permission_required = 'programs.change_parent'

    def get_success_url(self):
        next_url = self.request.GET.get('next')
        return next_url or reverse('parent_edit', args=[self.object.pk])


# --- Payment create ---
class ProgramPaymentCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Payment
    form_class = PaymentForm
    template_name = 'programs/payment_form.html'
    permission_required = 'programs.add_payment'

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['program'] = self.program
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['program'] = self.program
        return ctx

    def form_valid(self, form):
        # Optionally default amount to the fee amount if not provided
        obj = form.save(commit=False)
        if not obj.amount:
            obj.amount = obj.fee.amount
        obj.save()
        messages.success(self.request, 'Payment recorded successfully.')
        return redirect('program_detail', pk=self.program.pk)


class ProgramPaymentDetailView(LoginRequiredMixin, View):
    def get(self, request, pk, payment_id):
        program = get_object_or_404(Program, pk=pk)
        payment = get_object_or_404(Payment, pk=payment_id)
        # Ensure payment belongs to this program
        if payment.fee.program_id != program.id:
            messages.error(request, "Payment does not belong to this program.")
            return redirect('program_detail', pk=program.pk)
        student = payment.student
        # Ensure enrollment
        if not Enrollment.objects.filter(student=student, program=program).exists():
            messages.error(request, f"{student} is not enrolled in {program}.")
            return redirect('program_detail', pk=program.pk)
        from django.shortcuts import render
        return render(request, 'programs/payment_detail.html', {
            'program': program,
            'student': student,
            'payment': payment,
        })


class ProgramPaymentPrintView(LoginRequiredMixin, View):
    def get(self, request, pk, payment_id):
        program = get_object_or_404(Program, pk=pk)
        payment = get_object_or_404(Payment, pk=payment_id)
        if payment.fee.program_id != program.id:
            messages.error(request, "Payment does not belong to this program.")
            return redirect('program_detail', pk=program.pk)
        student = payment.student
        if not Enrollment.objects.filter(student=student, program=program).exists():
            messages.error(request, f"{student} is not enrolled in {program}.")
            return redirect('program_detail', pk=program.pk)
        from django.shortcuts import render
        return render(request, 'programs/payment_print.html', {
            'program': program,
            'student': student,
            'payment': payment,
        })


class ProgramSlidingScaleCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = SlidingScale
    form_class = SlidingScaleForm
    template_name = 'programs/sliding_scale_form.html'
    permission_required = 'programs.add_slidingscale'

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['program'] = self.program
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['program'] = self.program
        return ctx

    def form_valid(self, form):
        obj = form.save(commit=False)
        obj.program = self.program
        obj.save()
        messages.success(self.request, 'Sliding scale saved successfully.')
        return redirect('program_detail', pk=self.program.pk)


class ProgramStudentBalanceView(LoginRequiredMixin, View):
    def get(self, request, pk, student_id):
        program = get_object_or_404(Program, pk=pk)
        student = get_object_or_404(Student, pk=student_id)
        # Ensure enrollment
        if not Enrollment.objects.filter(student=student, program=program).exists():
            messages.error(request, f"{student} is not enrolled in {program}.")
            return redirect('program_detail', pk=program.pk)

        # Gather entries: fees (program), sliding scale (if exists), and payments (student for program's fees)
        entries = []
        # Fees: positive amounts
        # Use the editable fee.date when provided; otherwise fall back to created_at
        fees = Fee.objects.filter(program=program)
        for fee in fees:
            # If this fee has explicit assignments, include only if this student is assigned
            if fee.assignments.exists() and not fee.assignments.filter(student=student).exists():
                continue
            fee_date = fee.date or (fee.created_at.date() if fee.created_at else None)
            entries.append({
                'date': fee_date,
                'type': 'Fee',
                'name': fee.name,
                'amount': fee.amount,
            })
        # Sliding scale: negative amount (discount), include if exists
        sliding = SlidingScale.objects.filter(student=student, program=program).first()
        # Compute total fees first to apply percent-based discount
        from decimal import Decimal
        total_fees_for_discount = sum([fee.amount for fee in Fee.objects.filter(program=program)], start=Decimal('0'))
        if sliding and sliding.percent is not None:
            discount = (total_fees_for_discount * sliding.percent / Decimal('100'))
            entries.append({
                'date': sliding.created_at.date(),
                'type': 'Sliding Scale',
                'name': f"Sliding scale ({sliding.percent}%)",
                'amount': -discount,
            })
        # Payments: negative amounts
        payments = Payment.objects.filter(student=student, fee__program=program)
        for p in payments:
            entries.append({
                'date': p.paid_on,
                'type': 'Payment',
                'name': f"Payment for {p.fee.name}",
                'amount': -p.amount,
                'payment_id': p.id,
            })

        # Sort by date (editable fee date, sliding scale created_at, payment paid_on)
        # Ensure None dates sort last
        entries.sort(key=lambda e: (e['date'] is None, e['date'], e['type']))

        # Totals and balance
        total_fees = sum([e['amount'] for e in entries if e['type'] == 'Fee'])
        total_sliding = -sum([e['amount'] for e in entries if e['type'] == 'Sliding Scale'])  # positive figure
        total_payments = -sum([e['amount'] for e in entries if e['type'] == 'Payment'])  # positive figure
        balance = total_fees - total_sliding - total_payments

        from django.shortcuts import render
        return render(request, 'programs/balance_sheet.html', {
            'program': program,
            'student': student,
            'entries': entries,
            'total_fees': total_fees,
            'total_sliding': total_sliding,
            'total_payments': total_payments,
            'balance': balance,
        })


class ProgramStudentBalancePrintView(LoginRequiredMixin, View):
    def get(self, request, pk, student_id):
        program = get_object_or_404(Program, pk=pk)
        student = get_object_or_404(Student, pk=student_id)
        # Ensure enrollment
        if not Enrollment.objects.filter(student=student, program=program).exists():
            messages.error(request, f"{student} is not enrolled in {program}.")
            return redirect('program_detail', pk=program.pk)

        # Gather entries similar to balance sheet
        entries = []
        fees = Fee.objects.filter(program=program)
        for fee in fees:
            if fee.assignments.exists() and not fee.assignments.filter(student=student).exists():
                continue
            fee_date = fee.date or (fee.created_at.date() if fee.created_at else None)
            entries.append({
                'date': fee_date,
                'type': 'Fee',
                'name': fee.name,
                'amount': fee.amount,
            })
        sliding = SlidingScale.objects.filter(student=student, program=program).first()
        from decimal import Decimal
        total_fees_for_discount = sum([fee.amount for fee in Fee.objects.filter(program=program)], start=Decimal('0'))
        if sliding and sliding.percent is not None:
            discount = (total_fees_for_discount * sliding.percent / Decimal('100'))
            entries.append({
                'date': sliding.created_at.date(),
                'type': 'Sliding Scale',
                'name': f"Sliding scale ({sliding.percent}%)",
                'amount': -discount,
            })
        payments = Payment.objects.filter(student=student, fee__program=program)
        for p in payments:
            entries.append({
                'date': p.paid_on,
                'type': 'Payment',
                'name': f"Payment for {p.fee.name}",
                'amount': -p.amount,
                'payment_id': p.id,
            })

        entries.sort(key=lambda e: (e['date'] is None, e['date'], e['type']))

        total_fees = sum([e['amount'] for e in entries if e['type'] == 'Fee'])
        total_sliding = -sum([e['amount'] for e in entries if e['type'] == 'Sliding Scale'])
        total_payments = -sum([e['amount'] for e in entries if e['type'] == 'Payment'])
        balance = total_fees - total_sliding - total_payments

        from django.shortcuts import render
        return render(request, 'programs/balance_sheet_print.html', {
            'program': program,
            'student': student,
            'entries': entries,
            'total_fees': total_fees,
            'total_sliding': total_sliding,
            'total_payments': total_payments,
            'balance': balance,
        })


class ProgramFeeSelectView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'programs.change_fee'
    template_name = 'programs/fee_select.html'

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        from django.shortcuts import render
        fees = Fee.objects.filter(program=self.program).order_by('name')
        return render(request, self.template_name, {'program': self.program, 'fees': fees})


class ProgramFeeAssignmentEditView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'programs.change_fee'
    template_name = 'programs/fee_assignment_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs['pk'])
        self.fee = get_object_or_404(Fee, pk=kwargs['fee_id'], program=self.program)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk, fee_id):
        form = FeeAssignmentEditForm(program=self.program, fee=self.fee)
        from django.shortcuts import render
        return render(request, self.template_name, {'program': self.program, 'fee': self.fee, 'form': form})

    def post(self, request, pk, fee_id):
        form = FeeAssignmentEditForm(request.POST, program=self.program, fee=self.fee)
        if form.is_valid():
            form.save()
            messages.success(request, 'Fee applicability saved.')
            return redirect('program_fee_assignments', pk=self.program.pk, fee_id=self.fee.pk)
        from django.shortcuts import render
        return render(request, self.template_name, {'program': self.program, 'fee': self.fee, 'form': form})


class ProgramFeeCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    permission_required = 'programs.add_fee'
    model = Fee
    form_class = FeeForm
    template_name = 'programs/fee_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['program'] = self.program
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['program'] = self.program
        ctx['is_create'] = True
        return ctx

    def form_valid(self, form):
        messages.success(self.request, 'Fee created.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('program_fee_assignments', kwargs={'pk': self.program.pk, 'fee_id': self.object.pk})


class ProgramFeeUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    permission_required = 'programs.change_fee'
    model = Fee
    form_class = FeeForm
    template_name = 'programs/fee_form.html'
    pk_url_kwarg = 'fee_id'

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return get_object_or_404(Fee, pk=self.kwargs['fee_id'], program=self.program)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['program'] = self.program
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['program'] = self.program
        ctx['is_create'] = False
        return ctx

    def form_valid(self, form):
        messages.success(self.request, 'Fee updated.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('program_fee_assignments', kwargs={'pk': self.program.pk, 'fee_id': self.object.pk})


class ApplyProgramSelectView(View):
    template_name = 'apply/select_program.html'

    def get(self, request):
        from django.shortcuts import render
        from django.utils import timezone
        # Show active programs grouped by timing (future/current/past)
        today = timezone.localdate()
        programs = list(Program.objects.filter(active=True))

        def status(prog):
            sd = prog.start_date
            ed = prog.end_date
            if sd and sd > today:
                return 'future'
            if ed and ed < today:
                return 'past'
            # If only start or only end or none: treat as current if not clearly future/past
            return 'current'

        def sort_key(prog):
            sd = prog.start_date
            return (sd is None, sd or today, prog.name or '')

        future_programs = sorted([p for p in programs if status(p) == 'future'], key=sort_key)
        current_programs = sorted([p for p in programs if status(p) == 'current'], key=sort_key)
        past_programs = sorted([p for p in programs if status(p) == 'past'], key=sort_key)
        # Keep form in context for possible fallback
        form = ProgramApplySelectForm()
        return render(request, self.template_name, {
            'form': form,
            'future_programs': future_programs,
            'current_programs': current_programs,
            'past_programs': past_programs,
        })

    def post(self, request):
        from django.shortcuts import redirect, render
        form = ProgramApplySelectForm(request.POST)
        if form.is_valid():
            program = form.cleaned_data['program']
            return redirect('apply_program', program_id=program.pk)
        return render(request, self.template_name, {'form': form})


class ApplyStudentView(View):
    template_name = 'apply/form.html'

    def _program_status(self, program):
        from django.utils import timezone
        today = timezone.localdate()
        sd = program.start_date
        ed = program.end_date
        if sd and sd > today:
            return 'future'
        if ed and ed < today:
            return 'past'
        return 'current'

    def get(self, request, program_id):
        from django.shortcuts import render, get_object_or_404, redirect
        program = get_object_or_404(Program, pk=program_id)
        status = self._program_status(program)
        if status != 'future':
            if status == 'current':
                messages.info(request, 'Applications for this program are closed. For current programs, please contact us at info@girlsofsteelrobotics.org.')
            else:
                messages.error(request, 'Applications are closed for this program.')
            return redirect('apply_start')
        form = StudentApplicationForm(initial={'program': program})
        return render(request, self.template_name, {'form': form, 'program': program})

    def post(self, request, program_id):
        from django.shortcuts import render, get_object_or_404, redirect
        program = get_object_or_404(Program, pk=program_id)
        status = self._program_status(program)
        if status != 'future':
            if status == 'current':
                messages.info(request, 'Applications for this program are closed. For current programs, please contact us at info@girlsofsteelrobotics.org.')
            else:
                messages.error(request, 'Applications are closed for this program.')
            return redirect('apply_start')
        form = StudentApplicationForm(request.POST)
        if form.is_valid():
            app = form.save()
            messages.success(request, 'Application submitted! We will be in touch soon.')
            return redirect('apply_thanks')
        return render(request, self.template_name, {'form': form, 'program': program})


class ApplyThanksView(View):
    template_name = 'apply/thanks.html'

    def get(self, request):
        from django.shortcuts import render
        return render(request, self.template_name)


class ProgramDuesOwedView(LoginRequiredMixin, View):
    """
    Lists all students enrolled in a specific program and the total amount each currently owes
    for that program, using the same balance computation as the per-program balance sheet.
    """
    template_name = 'programs/dues_owed.html'

    def _program_balance_for_student(self, student, program):
        # Reproduce ProgramStudentBalanceView totals for a given student+program
        from decimal import Decimal
        # Fees applicable to the student (respect fee assignments)
        applicable_fees = []
        for fee in Fee.objects.filter(program=program):
            if fee.assignments.exists() and not fee.assignments.filter(student=student).exists():
                continue
            applicable_fees.append(fee.amount)
        total_fees = sum(applicable_fees, start=Decimal('0'))

        # Sliding scale percent discount based on total program fees (per balance sheet logic)
        sliding = SlidingScale.objects.filter(student=student, program=program).first()
        total_fees_for_discount = sum([f.amount for f in Fee.objects.filter(program=program)], start=Decimal('0'))
        total_sliding = Decimal('0')
        if sliding and sliding.percent is not None:
            total_sliding = (total_fees_for_discount * sliding.percent / Decimal('100'))

        # Payments made by student for fees in this program
        total_payments = sum([p.amount for p in Payment.objects.filter(student=student, fee__program=program)], start=Decimal('0'))

        balance = total_fees - total_sliding - total_payments
        return balance

    def get(self, request, pk):
        from django.shortcuts import render
        program = get_object_or_404(Program, pk=pk)
        # Only students enrolled in this program
        students = (
            Student.objects.filter(enrollment__program=program)
            .select_related('school')
            .order_by(Lower(Coalesce('first_name', 'legal_first_name')), Lower('last_name'))
        )

        rows = []
        grand_total = 0
        for s in students:
            balance_sum = self._program_balance_for_student(s, program)
            rows.append({
                'student': s,
                'amount_owed': balance_sum,
            })
            grand_total += balance_sum


        return render(request, self.template_name, {
            'program': program,
            'rows': rows,
            'grand_total': grand_total,
        })