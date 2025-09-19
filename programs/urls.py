from django.urls import path
from django.contrib.auth.decorators import login_required, permission_required
from django.views.generic import TemplateView
from .views import (
    ProgramListView, ProgramDetailView, ProgramCreateView,
    ProgramStudentAddView, ProgramStudentRemoveView, ProgramStudentQuickCreateView,
    StudentUpdateView, StudentListView, StudentCreateView, StudentPhotoListView, ProgramStudentPhotoListView, StudentDetailView,
    ParentCreateView, ParentUpdateView, ParentListView,
    MentorListView, MentorCreateView, MentorUpdateView,
    ProgramPaymentCreateView, ProgramPaymentDetailView, ProgramPaymentPrintView,
    ProgramSlidingScaleCreateView,
    ProgramStudentBalanceView,
    SchoolListView, SchoolCreateView, SchoolUpdateView,
    ProgramEmailView,
    ProgramFeeSelectView,
    ProgramFeeAssignmentEditView,
    ProgramFeeCreateView,
    ProgramFeeUpdateView,
    ImportDashboardView,
    StudentImportView, ParentImportView, MentorImportView, SchoolImportView,
    StudentEmergencyContactsView, StudentsByGradeView, StudentsBySchoolView,
    AlumniListView, StudentConvertToAlumniView, StudentBulkConvertToAlumniView,
    ProgramStudentBalancePrintView,
    ProgramDuesOwedView,
)

urlpatterns = [
    # Programs
    path('', login_required(ProgramListView.as_view()), name='program_list'),
    path('new/', permission_required('programs.add_program')(ProgramCreateView.as_view()), name='program_create'),
    path('<int:pk>/', login_required(ProgramDetailView.as_view()), name='program_detail'),
    path('messaging/', login_required(ProgramEmailView.as_view()), name='program_messaging'),
    path('<int:pk>/email/', login_required(ProgramEmailView.as_view()), name='program_email'),
    path('<int:pk>/dues/', login_required(ProgramDuesOwedView.as_view()), name='program_dues_owed'),
    path('<int:pk>/photos/', login_required(ProgramStudentPhotoListView.as_view()), name='program_student_photos'),

    # List pages
    path('imports/', login_required(ImportDashboardView.as_view()), name='import_dashboard'),
    # CSV template downloads served via templates to avoid static collection issues
    path('imports/samples/students.csv', permission_required('programs.add_student')(TemplateView.as_view(template_name='samples/students_sample.csv', content_type='text/csv')), name='students_sample_csv'),
    path('imports/samples/parents.csv', permission_required('programs.add_parent')(TemplateView.as_view(template_name='samples/parents_sample.csv', content_type='text/csv')), name='parents_sample_csv'),
    path('imports/samples/mentors.csv', permission_required('programs.add_mentor')(TemplateView.as_view(template_name='samples/mentors_sample.csv', content_type='text/csv')), name='mentors_sample_csv'),
    path('imports/samples/schools.csv', permission_required('programs.add_school')(TemplateView.as_view(template_name='samples/schools_sample.csv', content_type='text/csv')), name='schools_sample_csv'),
    path('students/', login_required(StudentListView.as_view()), name='student_list'),
    path('students/emergency-contacts/', login_required(StudentEmergencyContactsView.as_view()), name='student_emergency_contacts'),
    path('students/by-grade/', login_required(StudentsByGradeView.as_view()), name='students_by_grade'),
    path('students/by-school/', login_required(StudentsBySchoolView.as_view()), name='students_by_school'),
    path('students/import/', permission_required('programs.add_student')(StudentImportView.as_view()), name='student_import'),
    path('students/photos/', login_required(StudentPhotoListView.as_view()), name='student_photos'),
    path('students/new/', permission_required('programs.add_student')(StudentCreateView.as_view()), name='student_create'),
    path('students/convert-to-alumni/', permission_required('programs.change_student')(StudentBulkConvertToAlumniView.as_view()), name='student_bulk_convert_select'),
    path('students/<int:pk>/', login_required(StudentDetailView.as_view()), name='student_detail'),
    path('parents/', login_required(ParentListView.as_view()), name='parent_list'),
    path('parents/import/', permission_required('programs.add_parent')(ParentImportView.as_view()), name='parent_import'),
    path('mentors/', login_required(MentorListView.as_view()), name='mentor_list'),
    path('alumni/', login_required(AlumniListView.as_view()), name='alumni_list'),
    path('mentors/import/', permission_required('programs.add_mentor')(MentorImportView.as_view()), name='mentor_import'),
    path('schools/import/', permission_required('programs.add_school')(SchoolImportView.as_view()), name='school_import'),
    path('mentors/new/', permission_required('programs.add_mentor')(MentorCreateView.as_view()), name='mentor_create'),
    path('mentors/<int:pk>/edit/', permission_required('programs.change_mentor')(MentorUpdateView.as_view()), name='mentor_edit'),
    path('schools/', login_required(SchoolListView.as_view()), name='school_list'),

    # Program-specific student management
    path('<int:pk>/students/add/', permission_required('programs.change_student')(ProgramStudentAddView.as_view()), name='program_student_add'),
    path('<int:pk>/students/quick-create/', permission_required('programs.add_student')(ProgramStudentQuickCreateView.as_view()), name='program_student_quick_create'),
    path('<int:pk>/students/<int:student_id>/remove/', permission_required('programs.change_student')(ProgramStudentRemoveView.as_view()), name='program_student_remove'),
    path('<int:pk>/students/<int:student_id>/balance/', login_required(ProgramStudentBalanceView.as_view()), name='program_student_balance'),
    path('<int:pk>/students/<int:student_id>/balance/print/', login_required(ProgramStudentBalancePrintView.as_view()), name='program_student_balance_print'),

    # Payments
    path('<int:pk>/payments/new/', permission_required('programs.add_payment')(ProgramPaymentCreateView.as_view()), name='program_payment_create'),
    path('<int:pk>/payments/<int:payment_id>/', login_required(ProgramPaymentDetailView.as_view()), name='program_payment_detail'),
    path('<int:pk>/payments/<int:payment_id>/print/', login_required(ProgramPaymentPrintView.as_view()), name='program_payment_print'),

    # Sliding scales
    path('<int:pk>/sliding-scales/new/', permission_required('programs.add_slidingscale')(ProgramSlidingScaleCreateView.as_view()), name='program_sliding_scale_create'),

    # Fee management
    path('<int:pk>/fees/manage/', permission_required('programs.change_fee')(ProgramFeeSelectView.as_view()), name='program_fee_select'),
    path('<int:pk>/fees/new/', permission_required('programs.add_fee')(ProgramFeeCreateView.as_view()), name='program_fee_create'),
    path('<int:pk>/fees/<int:fee_id>/edit/', permission_required('programs.change_fee')(ProgramFeeUpdateView.as_view()), name='program_fee_edit'),
    path('<int:pk>/fees/<int:fee_id>/assignments/', permission_required('programs.change_fee')(ProgramFeeAssignmentEditView.as_view()), name='program_fee_assignments'),

    # Student edit
    path('students/<int:pk>/edit/', permission_required('programs.change_student')(StudentUpdateView.as_view()), name='student_edit'),
    path('students/<int:pk>/convert-to-alumni/', permission_required('programs.change_student')(StudentConvertToAlumniView.as_view()), name='student_convert_to_alumni'),

    # Parent add/edit
    path('parents/new/', permission_required('programs.add_parent')(ParentCreateView.as_view()), name='parent_create'),
    path('parents/<int:pk>/edit/', permission_required('programs.change_parent')(ParentUpdateView.as_view()), name='parent_edit'),

    # School add/edit
    path('schools/new/', permission_required('programs.add_school')(SchoolCreateView.as_view()), name='school_create'),
    path('schools/<int:pk>/edit/', permission_required('programs.change_school')(SchoolUpdateView.as_view()), name='school_edit'),
]
