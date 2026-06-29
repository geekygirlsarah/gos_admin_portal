from audit.events import AuditEvent
from audit.service import log_event
from programs.permission_views import get_user_role

class SensitiveDataViewMixin:
    """
    Mixin to log when sensitive data is viewed by a mentor.
    Should be added to DetailViews or UpdateViews of sensitive models (Student, Adult).
    """
    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        
        # We only log if the user is a mentor (including lead mentors)
        role = get_user_role(request.user)
        if role in ["Mentor", "LeadMentor"]:
            # The object being viewed
            obj = getattr(self, "object", None)
            if not obj and hasattr(self, "get_object"):
                try:
                    obj = self.get_object()
                except Exception:
                    pass
            
            if obj:
                log_event(
                    event=AuditEvent.SENSITIVE_DATA_VIEW,
                    resource=obj,
                    request=request,
                    notes=f"Mentor {request.user.email} viewed {type(obj).__name__} data."
                )
        
        return response
