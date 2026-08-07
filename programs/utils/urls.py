"""Safe redirect/URL utilities."""

from __future__ import annotations

from urllib.parse import urlsplit

from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme


def get_safe_url(request, url):
    """
    Return a safe local URL for redirects.
    Accepts relative URLs and same-host absolute URLs, and always returns a
    relative path (or None if unsafe/not present).
    """
    url = str(url).strip()
    if not url:
        return None

    allowed_hosts = {request.get_host()}
    if not url_has_allowed_host_and_scheme(
        url=url,
        allowed_hosts=allowed_hosts,
        require_https=request.is_secure(),
    ):
        return None

    parsed = urlsplit(url)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    if parsed.fragment:
        path += "#" + parsed.fragment

    if not path.startswith("/") or path.startswith("//"):
        return None
    return path


def redirect_back(request, default):
    """
    Safely redirect back to the referer or a 'next' parameter,
    falling back to the provided default if neither is safe or present.
    """
    # Priority 1: 'next' parameter in POST or GET
    next_url = request.POST.get("next") or request.GET.get("next")

    # Priority 2: HTTP_REFERER
    referer = request.META.get("HTTP_REFERER")

    for url in [next_url, referer]:
        safe_url = get_safe_url(request, url)
        if safe_url:
            return redirect(safe_url)

    return redirect(default)


# ---------------------------------------------------------------------------
# Signed parent-resume URL helpers
# ---------------------------------------------------------------------------

from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.urls import reverse


def generate_signed_parent_url(application_id):
    signer = TimestampSigner()
    token = signer.sign(str(application_id))
    return reverse("apply_parent_resume", kwargs={"token": token})


def verify_signed_parent_token(token, max_age=86400):  # 24 hours
    signer = TimestampSigner()
    try:
        return signer.unsign(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
