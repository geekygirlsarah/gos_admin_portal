from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.shortcuts import redirect, render

from programs.models import Adult, Program, School

from .forms import (
    Parent1InfoForm,
    Parent2InfoForm,
    ParentVerificationForm,
    ProgramSelectionForm,
    RoleSelectionForm,
    StudentEssayForm,
    StudentInfoForm,
)
from .models import ApplicationOTP, StudentApplication


def apply_intro(request):
    return render(request, "apply/intro.html")


# For the wizard, the requirement for jumping back and forth suggests a custom state machine or
# a very flexible wizard.
# Steps:
# 1. Program Select
# 2. Role Select
# 3. Parent Verify (if parent)
# 4. Parent 1 Info (if parent)
# 5. Parent 2 Info (if parent)
# 6. Student Info
# 7. Student Essay
# 8. Confirmation

# I will implement a custom wizard-like view to handle the logic easily.


def apply_wizard(request, step=None):
    if step is None:
        return redirect("apply_step", step="intro")

    # Storage in session
    storage = request.session.get("apply_data", {})

    if step == "intro":
        if request.method == "POST":
            return redirect("apply_step", step="program")
        return render(request, "apply/intro.html")

    if step == "program":
        form = ProgramSelectionForm(
            request.POST or None, initial=storage.get("program_data")
        )
        if request.method == "POST" and form.is_valid():
            storage["program_id"] = form.cleaned_data["program"].id
            request.session["apply_data"] = storage
            return redirect("apply_step", step="role")
        return render(request, "apply/program.html", {"form": form})

    if step == "role":
        form = RoleSelectionForm(request.POST or None, initial=storage.get("role_data"))
        if request.method == "POST" and form.is_valid():
            role = form.cleaned_data["role"]
            storage["role"] = role
            request.session["apply_data"] = storage
            if role == "parent":
                return redirect("apply_step", step="parent_verify")
            else:
                return redirect("apply_step", step="student_info")
        return render(request, "apply/role.html", {"form": form})

    if step == "parent_verify":
        # OTP Logic here
        form = ParentVerificationForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            email = form.cleaned_data["email"]
            otp_code = form.cleaned_data.get("otp")

            if not otp_code:
                # Generate and send OTP
                otp = ApplicationOTP.generate_otp(email)
                # In real app, send email. For now, print or mock.
                print(f"OTP for {email}: {otp.code}")
                messages.info(request, f"A verification code has been sent to {email}.")
                return render(
                    request,
                    "apply/parent_verify.html",
                    {"form": form, "otp_sent": True},
                )
            else:
                otp = ApplicationOTP.objects.filter(email=email, code=otp_code).last()
                if otp and otp.is_valid():
                    otp.is_verified = True
                    otp.save()
                    storage["parent_email"] = email
                    # Pre-fill logic
                    adult = Adult.objects.filter(email__iexact=email).first()
                    if adult:
                        storage["parent1_data"] = {
                            "parent1_preferred_first_name": adult.preferred_name
                            or adult.legal_first_name,
                            "parent1_legal_first_name": adult.legal_first_name,
                            "parent1_last_name": adult.last_name,
                            "parent1_phone_number": adult.phone_number,
                            "parent1_email": adult.email,
                        }
                    request.session["apply_data"] = storage
                    return redirect("apply_step", step="parent1_info")
                else:
                    messages.error(request, "Invalid or expired OTP.")

        return render(request, "apply/parent_verify.html", {"form": form})

    if step == "parent1_info":
        form = Parent1InfoForm(
            request.POST or None, initial=storage.get("parent1_data")
        )
        if request.method == "POST" and form.is_valid():
            storage["parent1_data"] = form.cleaned_data
            request.session["apply_data"] = storage
            return redirect("apply_step", step="parent2_info")
        return render(request, "apply/parent1_info.html", {"form": form})

    if step == "parent2_info":
        form = Parent2InfoForm(
            request.POST or None, initial=storage.get("parent2_data")
        )
        if request.method == "POST" and form.is_valid():
            storage["parent2_data"] = form.cleaned_data
            request.session["apply_data"] = storage
            if storage.get("role") == "parent":
                # Jump back to student info as requested (3b)
                return redirect("apply_step", step="student_info")
            else:
                return redirect("apply_step", step="confirm")
        return render(request, "apply/parent2_info.html", {"form": form})

    if step == "student_info":
        form = StudentInfoForm(
            request.POST or None, initial=storage.get("student_data")
        )

        # 4a/4b/4c logic
        # 4c. If parent is filling it out, no need to verify student info.
        is_parent = storage.get("role") == "parent"

        if request.method == "POST" and form.is_valid():
            email = form.cleaned_data.get("email")
            # 4a. If they have an email, fill that in and then they can get a one-time password to confirm them.
            if email and not is_parent:
                otp_code = request.POST.get("student_otp")
                if not otp_code:
                    otp = ApplicationOTP.generate_otp(email)
                    print(f"Student OTP for {email}: {otp.code}")
                    messages.info(
                        request, f"A verification code has been sent to {email}."
                    )
                    return render(
                        request,
                        "apply/student_info.html",
                        {"form": form, "otp_sent": True},
                    )
                else:
                    otp = ApplicationOTP.objects.filter(
                        email=email, code=otp_code
                    ).last()
                    if otp and otp.is_valid():
                        otp.is_verified = True
                        otp.save()
                    else:
                        messages.error(request, "Invalid or expired OTP.")
                        return render(
                            request,
                            "apply/student_info.html",
                            {"form": form, "otp_sent": True},
                        )

            # 4b. If they don't have an email, they can use first name, school, and birthday.
            # (Implicitly handled by form validation if email is optional)

            # Convert objects to IDs for session serialization
            data = form.cleaned_data.copy()
            if data.get("date_of_birth"):
                data["date_of_birth"] = data["date_of_birth"].isoformat()
            if data.get("school"):
                data["school"] = data["school"].id

            storage["student_data"] = data
            request.session["apply_data"] = storage
            return redirect("apply_step", step="student_essay")
        return render(request, "apply/student_info.html", {"form": form})

    if step == "student_essay":
        form = StudentEssayForm(request.POST or None, initial=storage.get("essay_data"))
        if request.method == "POST" and form.is_valid():
            storage["essay_data"] = form.cleaned_data
            request.session["apply_data"] = storage
            if storage.get("role") == "parent":
                return redirect("apply_step", step="confirm")
            else:
                # If student filling it out, they still need to do parents?
                # "3a. A student should jump to step 4, then continue through the rest."
                # Rest includes parents.
                return redirect("apply_step", step="parent_verify")
        return render(request, "apply/student_essay.html", {"form": form})

    if step == "confirm":
        if request.method == "POST":
            # Finalize
            program = Program.objects.get(id=storage["program_id"])
            app = StudentApplication(program=program)
            # Fill from all data
            for key in ["student_data", "essay_data", "parent1_data", "parent2_data"]:
                data = storage.get(key, {})
                for field, value in data.items():
                    if hasattr(app, field):
                        # Special handling for ForeignKeys if stored as IDs
                        if field == "school" and value:
                            setattr(
                                app,
                                field,
                                (
                                    School.objects.get(id=value)
                                    if isinstance(value, int)
                                    else value
                                ),
                            )
                        else:
                            setattr(app, field, value)
            app.save()

            # Notify
            try:
                subject = f"Girls of Steel Application Received: {app.preferred_first_name} {app.last_name}"
                message = f"Thank you for applying to {app.program.name}!\n\nWe have received your application and will review it soon."
                recipient_list = [app.parent1_email]
                if app.email:
                    recipient_list.append(app.email)
                if app.parent2_email:
                    recipient_list.append(app.parent2_email)

                send_mail(
                    subject,
                    message,
                    settings.DEFAULT_FROM_EMAIL,
                    recipient_list,
                    fail_silently=True,
                )
            except Exception as e:
                print(f"Failed to send email: {e}")

            del request.session["apply_data"]
            return render(request, "apply/thanks.html", {"application": app})

        return render(request, "apply/confirm.html", {"storage": storage})

    return redirect("apply_step", step="intro")
