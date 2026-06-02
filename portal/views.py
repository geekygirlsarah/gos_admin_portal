from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "portal/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        adult = getattr(self.request.user, "adult_profile", None)
        context["adult"] = adult
        if adult:
            context["is_parent"] = adult.is_parent
            context["is_mentor"] = adult.is_mentor
            context["is_alumni"] = adult.is_alumni

            if adult.is_parent:
                # Get students linked to this adult with their relationship type
                context["students"] = adult.all_students()

            if adult.is_mentor:
                # Placeholder for mentor-specific data (e.g. active programs)
                pass

            if adult.is_alumni:
                # Get student record if linked
                context["student_record"] = adult.student_record
                if adult.student_record:
                    # Get enrollments for the student record
                    from programs.models import Enrollment
                    context["enrollments"] = Enrollment.objects.filter(
                        student=adult.student_record
                    ).select_related("program", "team")
        return context
