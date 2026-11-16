import time
from unittest.mock import patch

from django.urls import reverse

from programs.constants import OTP_TTL_SECONDS


def grant_applicant_document_access(client, application):
    """Simulate a successful email-code challenge for document workflow tests."""
    session = client.session
    session[
        f"application_document_access:{application.organization_id}:{application.application_id}"
    ] = {
        "organization_id": application.organization_id,
        "email": (application.email or "").strip().lower(),
        "expires_at": int(time.time()) + OTP_TTL_SECONDS,
    }
    session.save()


def verify_application_browser(
    client, application, code="123456", expected_recipient=None
):
    """Verify browser access through the public OTP endpoints for a test client."""
    expected_recipient = expected_recipient or application.email
    url = reverse(
        "apply_browser_verification", kwargs={"app_id": application.application_id}
    )
    with patch("applications.views.browser.generate_otp_code", return_value=code):
        with patch("applications.views.browser.send_otp_email") as send_email:
            send_response = client.post(url, {"action": "send"})

    if send_response.status_code != 302:
        raise AssertionError(
            f"Expected browser OTP send to redirect, got {send_response.status_code}"
        )
    if not send_email.called:
        raise AssertionError("Expected browser OTP to be sent")
    if send_email.call_args.kwargs.get("recipient") != expected_recipient:
        raise AssertionError(
            f"Browser OTP was not sent to the expected recipient {expected_recipient}"
        )

    verify_response = client.post(url, {"code": code})
    if verify_response.status_code != 302:
        raise AssertionError(
            f"Expected browser OTP verification to redirect, got "
            f"{verify_response.status_code}"
        )
    return verify_response
