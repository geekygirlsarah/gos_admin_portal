import secrets
import string

from django.conf import settings
from django.core.mail import send_mail
from django.core.signing import BadSignature, SignatureExpired, Signer, TimestampSigner
from django.urls import reverse


def generate_otp(length=6):
    return "".join(secrets.choice(string.digits) for _ in range(length))


def send_otp_email(email, otp):
    subject = "Your GoS Admin Portal Verification Code"
    message = (
        f"Your verification code is: {otp}\n\nThis code will expire in 10 minutes."
    )
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [email])


def generate_signed_parent_url(application_id):
    signer = TimestampSigner()
    token = signer.sign(str(application_id))
    # We'll define the URL name later, for now using a placeholder
    return reverse("apply_parent_resume", kwargs={"token": token})


def verify_signed_parent_token(token, max_age=86400):  # 24 hours
    signer = TimestampSigner()
    try:
        application_id = signer.unsign(token, max_age=max_age)
        return application_id
    except (BadSignature, SignatureExpired):
        return None
