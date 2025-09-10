from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import ListView, DetailView, CreateView, UpdateView, View

from django.core.mail import EmailMultiAlternatives, get_connection
from django.utils.html import strip_tags
from django.conf import settings
from django.db.models.functions import Coalesce, Lower

from .models import Program, Student, Enrollment, Parent, Mentor, Payment, SlidingScale, Fee, School
from .forms import (
    StudentForm,
    AddExistingStudentToProgramForm,
    QuickCreateStudentForm,
    ParentForm,
    PaymentForm,
    SlidingScaleForm,
    SchoolForm,
    MentorForm,
    ProgramEmailForm,
    ProgramFeeSelectForm,
    FeeAssignmentEditForm,
)


class ProgramListView(ListView):
    model = Program
    template_name = 'home.html'  # landing page
    context_object_name = 'programs'

    def get_queryset(self):
        from django.db.models import F
        return Program.objects.all().order_by(F('year').desc(nulls_last=True), 'name')


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


class ParentListView(LoginRequiredMixin, ListView):
    model = Parent
    template_name = 'parents/list.html'
    context_object_name = 'parents'

    def get_queryset(self):
        # Prefetch related students to avoid N+1 queries and order by name
        return Parent.objects.all().prefetch_related('students').order_by('last_name', 'first_name')


class MentorListView(LoginRequiredMixin, ListView):
    model = Mentor
    template_name = 'mentors/list.html'
    context_object_name = 'mentors'


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
                parent_emails = Parent.objects.filter(students__programs=prog).values_list('email', flat=True)
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
        ctx['enrolled_students'] = program.students.select_related('user').all().order_by('first_name', 'last_name')
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
    fields = ['name', 'description', 'year', 'active']
    template_name = 'programs/form.html'

    def get_success_url(self):
        return reverse('program_detail', args=[self.object.pk])


# --- Student edit ---
class StudentUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Student
    form_class = StudentForm
    template_name = 'students/form.html'
    permission_required = 'programs.change_student'

    def get_success_url(self):
        next_url = self.request.GET.get('next')
        return next_url or reverse('student_edit', args=[self.object.pk])


class StudentCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Student
    form_class = StudentForm
    template_name = 'students/form.html'
    permission_required = 'programs.add_student'

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
                'date': p.paid_at,
                'type': 'Payment',
                'name': f"Payment for {p.fee.name}",
                'amount': -p.amount,
            })

        # Sort by date (editable fee date, sliding scale created_at, payment paid_at)
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


class ProgramFeeSelectView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'programs.change_fee'
    template_name = 'programs/fee_select.html'

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        form = ProgramFeeSelectForm(program=self.program)
        from django.shortcuts import render
        return render(request, self.template_name, {'program': self.program, 'form': form})

    def post(self, request, pk):
        form = ProgramFeeSelectForm(request.POST, program=self.program)
        if form.is_valid():
            fee = form.cleaned_data['fee']
            return redirect('program_fee_assignments', pk=self.program.pk, fee_id=fee.pk)
        from django.shortcuts import render
        return render(request, self.template_name, {'program': self.program, 'form': form})


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