from programs.models import Program, Enrollment
from programs.permission_views import get_user_role
from django.urls import resolve, Resolver404

def active_program(request):
    if not request.user.is_authenticated:
        return {}

    program = None
    program_id = None

    # 1. Check if we are in a program-specific URL
    try:
        match = resolve(request.path)
        # Check if the view belongs to the programs app and has a 'pk' or 'program_id'
        if match.app_name == 'programs' or 'programs' in match.view_name:
            if 'pk' in match.kwargs and 'program' in match.view_name:
                program_id = match.kwargs['pk']
            elif 'program_id' in match.kwargs:
                program_id = match.kwargs['program_id']
    except Resolver404:
        pass

    # 2. Check session
    if not program_id:
        program_id = request.session.get('active_program_id')

    # 3. If still no program_id, check if user has only one program
    if not program_id:
        role = get_user_role(request.user)
        if role == 'Parent':
            enrollments = Enrollment.objects.filter(student__adults__user=request.user).distinct()
            programs = Program.objects.filter(enrollment__in=enrollments).distinct()
            if programs.count() == 1:
                program = programs.first()
        elif role == 'Student':
            programs = Program.objects.filter(enrollment__student__user=request.user).distinct()
            if programs.count() == 1:
                program = programs.first()
        elif role == 'LeadMentor':
            active_programs = Program.objects.filter(active=True)
            if active_programs.count() == 1:
                program = active_programs.first()
        
        if program:
            program_id = program.id

    if program_id and not program:
        try:
            program = Program.objects.get(id=program_id)
        except (Program.DoesNotExist, ValueError):
            pass

    return {
        'current_program': program,
    }
