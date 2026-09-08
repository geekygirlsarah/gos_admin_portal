from django.contrib.auth.models import Group, User

from orders.models import Order, OrderItem, ShippingCarrier, Vendor
from programs.models import (
    Adult,
    Enrollment,
    Program,
    ProgramFeature,
    School,
    Student,
)


def make_program(name="Test Program", with_orders_feature=True):
    program = Program.objects.create(name=name, active=True)
    if with_orders_feature:
        feature, _ = ProgramFeature.objects.get_or_create(
            key="orders", defaults={"name": "Order Requests"}
        )
        program.features.add(feature)
    return program


def make_lead_mentor_user(username="lead_mentor", password="password123"):  # nosec B107
    user = User.objects.create_user(username=username, password=password)
    group, _ = Group.objects.get_or_create(name="LeadMentor")
    user.groups.add(group)
    return user


def make_mentor_user(username="mentor", password="password123"):  # nosec B107
    user = User.objects.create_user(username=username, password=password)
    Adult.objects.create(
        user=user,
        legal_first_name="Mentor",
        last_name="User",
        is_mentor=True,
        mentor_active=True,
    )
    return user


def make_student_user(
    username="student", password="password123", program=None  # nosec B107
):
    school = School.objects.get_or_create(name="Test School")[0]
    user = User.objects.create_user(username=username, password=password)
    student = Student.objects.create(
        user=user,
        legal_first_name="Test",
        last_name="Student",
        school=school,
        graduation_year=2030,
    )
    if program is not None:
        Enrollment.objects.create(student=student, program=program, active=True)
    return user


def make_parent_user(username="parent", password="password123"):  # nosec B107
    user = User.objects.create_user(username=username, password=password)
    Adult.objects.create(
        user=user, legal_first_name="Parent", last_name="User", is_parent=True
    )
    return user


def make_item(program, item_name="Hex Driver", requested_by=None, order=None, **kwargs):
    data = {
        "program": program,
        "item_name": item_name,
        "quantity": "2",
        "unit_price": "5.50",
        "url": "https://example.com/item",
        "notes": "test note",
    }
    data.update(kwargs)
    if requested_by is not None:
        data["requested_by"] = requested_by
    if order is not None:
        data["order"] = order
    return OrderItem.objects.create(**data)


def make_order(program, created_by=None, status=None, **kwargs):
    data = {"program": program}
    if created_by is not None:
        data["created_by"] = created_by
    data.update(kwargs)
    order = Order.objects.create(**data)
    if status:
        order.status = status
        order.save(update_fields=["status"])
    return order


def make_carrier(name="UPS", tracking_url_template="", **kwargs):
    data = {"name": name, "tracking_url_template": tracking_url_template}
    data.update(kwargs)
    return ShippingCarrier.objects.create(**data)


def make_vendor(name="McMaster-Carr", **kwargs):
    data = {
        "name": name,
        "website": f"https://{name.lower().replace(' ', '').replace('.', '')}.com",
        "notes": "Fast shipping",
    }
    data.update(kwargs)
    return Vendor.objects.create(**data)
