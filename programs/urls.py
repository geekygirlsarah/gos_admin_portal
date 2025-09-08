from django.urls import path
from django.contrib.auth.decorators import login_required, permission_required
from .views import (
    ProgramListView, ProgramDetailView, ProgramCreateView,
    ProgramStudentAddView, ProgramStudentRemoveView, ProgramStudentQuickCreateView,
    StudentUpdateView, StudentListView, StudentCreateView,
    ParentCreateView, ParentUpdateView, ParentListView,
    MentorListView, MentorCreateView, MentorUpdateView,
    ProgramPaymentCreateView,
    ProgramSlidingScaleCreateView,
    ProgramStudentBalanceView,
    SchoolListView, SchoolCreateView, SchoolUpdateView,
)

urlpatterns = [
    # Programs
    path('', login_required(ProgramListView.as_view()), name='program_list'),
    path('new/', permission_required('programs.add_program')(ProgramCreateView.as_view()), name='program_create'),
    path('<int:pk>/', login_required(ProgramDetailView.as_view()), name='program_detail'),

    # List pages
    path('students/', login_required(StudentListView.as_view()), name='student_list'),
    path('students/new/', permission_required('programs.add_student')(StudentCreateView.as_view()), name='student_create'),
    path('parents/', login_required(ParentListView.as_view()), name='parent_list'),
    path('mentors/', login_required(MentorListView.as_view()), name='mentor_list'),
    path('mentors/new/', permission_required('programs.add_mentor')(MentorCreateView.as_view()), name='mentor_create'),
    path('mentors/<int:pk>/edit/', permission_required('programs.change_mentor')(MentorUpdateView.as_view()), name='mentor_edit'),
    path('schools/', login_required(SchoolListView.as_view()), name='school_list'),

    # Program-specific student management
    path('<int:pk>/students/add/', permission_required('programs.change_student')(ProgramStudentAddView.as_view()), name='program_student_add'),
    path('<int:pk>/students/quick-create/', permission_required('programs.add_student')(ProgramStudentQuickCreateView.as_view()), name='program_student_quick_create'),
    path('<int:pk>/students/<int:student_id>/remove/', permission_required('programs.change_student')(ProgramStudentRemoveView.as_view()), name='program_student_remove'),
    path('<int:pk>/students/<int:student_id>/balance/', login_required(ProgramStudentBalanceView.as_view()), name='program_student_balance'),

    # Payments
    path('<int:pk>/payments/new/', permission_required('programs.add_payment')(ProgramPaymentCreateView.as_view()), name='program_payment_create'),

    # Sliding scales
    path('<int:pk>/sliding-scales/new/', permission_required('programs.add_slidingscale')(ProgramSlidingScaleCreateView.as_view()), name='program_sliding_scale_create'),

    # Student edit
    path('students/<int:pk>/edit/', permission_required('programs.change_student')(StudentUpdateView.as_view()), name='student_edit'),

    # Parent add/edit
    path('parents/new/', permission_required('programs.add_parent')(ParentCreateView.as_view()), name='parent_create'),
    path('parents/<int:pk>/edit/', permission_required('programs.change_parent')(ParentUpdateView.as_view()), name='parent_edit'),

    # School add/edit
    path('schools/new/', permission_required('programs.add_school')(SchoolCreateView.as_view()), name='school_create'),
    path('schools/<int:pk>/edit/', permission_required('programs.change_school')(SchoolUpdateView.as_view()), name='school_edit'),
]
