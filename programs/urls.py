from django.urls import path
from django.contrib.auth.decorators import login_required, permission_required
from .views import (
    ProgramListView, ProgramDetailView, ProgramCreateView,
    ProgramStudentAddView, ProgramStudentRemoveView, ProgramStudentQuickCreateView,
    StudentUpdateView, StudentListView,
    ParentCreateView, ParentUpdateView, ParentListView,
    MentorListView,
    ProgramPaymentCreateView,
    ProgramSlidingScaleCreateView,
)

urlpatterns = [
    # Programs
    path('', login_required(ProgramListView.as_view()), name='program_list'),
    path('new/', permission_required('programs.add_program')(ProgramCreateView.as_view()), name='program_create'),
    path('<int:pk>/', login_required(ProgramDetailView.as_view()), name='program_detail'),

    # List pages
    path('students/', login_required(StudentListView.as_view()), name='student_list'),
    path('parents/', login_required(ParentListView.as_view()), name='parent_list'),
    path('mentors/', login_required(MentorListView.as_view()), name='mentor_list'),

    # Program-specific student management
    path('<int:pk>/students/add/', permission_required('programs.change_student')(ProgramStudentAddView.as_view()), name='program_student_add'),
    path('<int:pk>/students/quick-create/', permission_required('programs.add_student')(ProgramStudentQuickCreateView.as_view()), name='program_student_quick_create'),
    path('<int:pk>/students/<int:student_id>/remove/', permission_required('programs.change_student')(ProgramStudentRemoveView.as_view()), name='program_student_remove'),

    # Payments
    path('<int:pk>/payments/new/', permission_required('programs.add_payment')(ProgramPaymentCreateView.as_view()), name='program_payment_create'),

    # Sliding scales
    path('<int:pk>/sliding-scales/new/', permission_required('programs.add_slidingscale')(ProgramSlidingScaleCreateView.as_view()), name='program_sliding_scale_create'),

    # Student edit
    path('students/<int:pk>/edit/', permission_required('programs.change_student')(StudentUpdateView.as_view()), name='student_edit'),

    # Parent add/edit
    path('parents/new/', permission_required('programs.add_parent')(ParentCreateView.as_view()), name='parent_create'),
    path('parents/<int:pk>/edit/', permission_required('programs.change_parent')(ParentUpdateView.as_view()), name='parent_edit'),
]
