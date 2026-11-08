import posixpath
from urllib.parse import unquote

from django.conf import settings
from django.http import HttpResponseNotFound
from django.views.static import serve

_PRIVATE_MEDIA_PREFIXES = (
    "agreement_submissions/",
    "application_documents/",
    "digital_signouts/",
    "guest_form_submissions/",
    "photos/adults/",
    "photos/mentors/",
    "photos/students/",
    "private/",
    "signed/",
    "student_documents/",
    "tax_forms/",
)


def serve_public_media(request, path):
    """Serve ordinary media while keeping sensitive uploads off public URLs."""
    normalized_path = posixpath.normpath(unquote(path).replace("\\", "/")).lstrip("/")
    if normalized_path == ".":
        normalized_path = ""
    normalized_path = normalized_path.casefold()
    if any(normalized_path.startswith(prefix) for prefix in _PRIVATE_MEDIA_PREFIXES):
        return HttpResponseNotFound()
    return serve(request, path, document_root=settings.MEDIA_ROOT)
