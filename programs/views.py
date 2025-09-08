from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import ListView, DetailView, CreateView, UpdateView, View

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
)


class ProgramListView(ListView):
    model = Program
    template_name = 'home.html'  # landing page
    context_object_name = 'programs'


class StudentListView(LoginRequiredMixin, ListView):
    model = Student
    template_name = 'students/list.html'
    context_object_name = 'students'


class ParentListView(LoginRequiredMixin, ListView):
    model = Parent
    template_name = 'parents/list.html'
    context_object_name = 'parents'


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


class ProgramDetailView(LoginRequiredMixin, DetailView):
    model = Program
    template_name = 'programs/detail.html'
    context_object_name = 'program'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        program = self.object
        ctx['enrolled_students'] = program.students.select_related('user').all().order_by('last_name', 'first_name')
        can_manage = self.request.user.has_perm('programs.change_student') or self.request.user.has_perm('programs.add_student')
        ctx['can_manage_students'] = can_manage
        ctx['can_add_payment'] = self.request.user.has_perm('programs.add_payment')
        ctx['can_add_sliding_scale'] = self.request.user.has_perm('programs.add_slidingscale')
        if can_manage:
            ctx['add_existing_form'] = AddExistingStudentToProgramForm(program=program)
            ctx['quick_create_form'] = QuickCreateStudentForm()
        return ctx


class ProgramCreateView(CreateView):
    model = Program
    fields = ['name', 'description', 'active']
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
        for fee in Fee.objects.filter(program=program).order_by('created_at'):
            entries.append({
                'date': fee.created_at.date(),
                'type': 'Fee',
                'name': fee.name,
                'amount': fee.amount,
            })
        # Sliding scale: negative amount (discount), include if exists
        sliding = SlidingScale.objects.filter(student=student, program=program).first()
        if sliding:
            entries.append({
                'date': sliding.created_at.date(),
                'type': 'Sliding Scale',
                'name': 'Sliding scale',
                'amount': -sliding.amount,
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

        # Sort by date
        entries.sort(key=lambda e: (e['date'], e['type']))

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