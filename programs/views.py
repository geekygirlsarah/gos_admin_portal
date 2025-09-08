from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import ListView, DetailView, CreateView, UpdateView, View

from .models import Program, Student, Enrollment, Parent, Mentor
from .forms import (
    StudentForm,
    AddExistingStudentToProgramForm,
    QuickCreateStudentForm,
    ParentForm,
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
        return reverse('parent_edit', args=[self.object.pk])


class ParentUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Parent
    form_class = ParentForm
    template_name = 'parents/form.html'
    permission_required = 'programs.change_parent'

    def get_success_url(self):
        next_url = self.request.GET.get('next')
        return next_url or reverse('parent_edit', args=[self.object.pk])