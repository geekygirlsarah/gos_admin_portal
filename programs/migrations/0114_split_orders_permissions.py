from django.db import migrations

LEGACY_ORDERS = "orders"
GRANULAR_SECTIONS = (
    "orders-view",
    "orders-request",
    "orders-manage",
    "orders-shipping",
)


def split_orders_rows(apps, schema_editor):
    """Replace the single ``orders`` permission section with the four granular
    sections.

    The legacy row's read flag maps to ``orders-view`` (seeing the order
    pages); its write flag maps to the action sections (placing requests,
    placing/managing orders, adding shipping).
    """
    RolePermission = apps.get_model("programs", "RolePermission")
    for row in RolePermission.objects.filter(section=LEGACY_ORDERS):
        RolePermission.objects.get_or_create(
            role=row.role,
            section="orders-view",
            defaults={"can_read": row.can_read, "can_write": row.can_read},
        )
        for section_code in GRANULAR_SECTIONS[1:]:
            RolePermission.objects.get_or_create(
                role=row.role,
                section=section_code,
                defaults={"can_read": row.can_read, "can_write": row.can_write},
            )
        row.delete()


def recombine_orders_rows(apps, schema_editor):
    """Reverse: collapse the granular sections back into a single ``orders``
    row per role."""
    RolePermission = apps.get_model("programs", "RolePermission")
    rows = RolePermission.objects.filter(section__in=GRANULAR_SECTIONS)
    for role in rows.values_list("role", flat=True).distinct():
        row_view = RolePermission.objects.filter(
            role=role, section="orders-view"
        ).first()
        row_request = RolePermission.objects.filter(
            role=role, section="orders-request"
        ).first()
        if row_view is None and row_request is None:
            continue
        can_read = row_view.can_read if row_view else row_request.can_read
        can_write = (
            row_request.can_write
            if row_request
            else (row_view.can_read if row_view else False)
        )
        RolePermission.objects.update_or_create(
            role=role,
            section=LEGACY_ORDERS,
            defaults={"can_read": can_read, "can_write": can_write},
        )
    RolePermission.objects.filter(section__in=GRANULAR_SECTIONS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("programs", "0113_add_orders_feature"),
    ]

    operations = [
        migrations.RunPython(split_orders_rows, recombine_orders_rows),
    ]
