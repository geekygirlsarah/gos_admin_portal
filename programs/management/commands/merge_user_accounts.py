"""
merge_user_accounts -- fold one duplicate User account into another.

Separate User accounts can accumulate for the same person (one keyed by a
personal email, one by their Andrew email). This command merges a "source"
account into a surviving "target" account:

* moves allauth EmailAddress records (keeping exactly one primary),
* moves groups and user permissions,
* re-points every FK/OneToOne reference from the source to the target,
* fills empty ``first_name``/``last_name`` on the target from the source,
* and removes the source User.

Read-only by default; pass ``--execute`` to actually perform the merge.

Usage::

    python manage.py merge_user_accounts --source <pk> --target <pk>      # report only
    python manage.py merge_user_accounts --source <pk> --target <pk> --execute
"""

from allauth.account.models import EmailAddress
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

User = get_user_model()

_SPECIAL = {EmailAddress._meta.label_lower}


def _user_fk_fields():
    """Yield (model, field) for every non-M2M relation to the User model."""
    for model in apps.get_models():
        # Skip proxy models (e.g. pghistory.MiddlewareEvents) whose "user" is a
        # computed ProxyField, not a real stored column.
        if model._meta.proxy or model._meta.managed is False:
            continue
        if model._meta.label_lower in _SPECIAL:
            continue
        for field in model._meta.get_fields():
            if (
                getattr(field, "is_relation", False)
                and hasattr(field, "related_model")
                and field.related_model is User
                and not getattr(field, "many_to_many", False)
            ):
                yield model, field


class Command(BaseCommand):
    help = (
        "Fold a duplicate User account (source) into a surviving account (target), "
        "re-linking all emails, permissions and references; read-only unless --execute."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source", type=int, required=True, help="PK of the account to merge away."
        )
        parser.add_argument(
            "--target", type=int, required=True, help="PK of the account to keep."
        )
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Actually apply the merge (read-only by default).",
        )

    def handle(self, *args, **options):
        source = User.objects.filter(pk=options["source"]).first()
        target = User.objects.filter(pk=options["target"]).first()
        if not source or not target:
            raise CommandError(
                "--source and --target must both exist "
                f"(got source={options['source']}, target={options['target']})."
            )
        if source.pk == target.pk:
            raise CommandError("--source and --target must be different users.")

        plan = self._build_plan(source, target)
        self._print_plan(source, target, plan)

        if not options["execute"]:
            self.stdout.write(
                "\nDry run - nothing changed. Re-run with --execute to apply."
            )
            return

        with transaction.atomic():
            self._apply(source, target, plan)
        self.stdout.write(
            self.style.SUCCESS(f"\nMerged #{options['source']} into #{target.pk}.")
        )

    # ------------------------------------------------------------------ plan
    def _build_plan(self, source, target):
        plan = {"emails": [], "m2m": [], "fk": [], "fields": []}

        for ea in EmailAddress.objects.filter(user=source):
            plan["emails"].append((ea.email, ea.primary, ea.verified))

        for through in (User.groups.through, User.user_permissions.through):
            plan["m2m"].append(
                (
                    through._meta.label_lower,
                    through.objects.filter(user_id=source.pk).count(),
                )
            )

        for model, field in _user_fk_fields():
            count = model.objects.filter(**{field.name: source}).count()
            if count:
                plan["fk"].append((model._meta.label_lower, field.name, count))

        for name in ("first_name", "last_name"):
            if not getattr(target, name) and getattr(source, name):
                plan["fields"].append((name, getattr(source, name)))

        return plan

    # ---------------------------------------------------------------- report
    def _print_plan(self, source, target, plan):
        self.stdout.write(
            self.style.SUCCESS(
                f"Merge account #{source.pk} ({source.username}) into "
                f"#{target.pk} ({target.username})"
            )
        )
        if plan["emails"]:
            self.stdout.write("\nEmail addresses to move:")
            for email, primary, verified in plan["emails"]:
                self.stdout.write(
                    f"  - {email} (primary={primary}, verified={verified})"
                )
        m2m = [(label, count) for label, count in plan["m2m"] if count]
        if m2m:
            self.stdout.write("\nGroup/permission memberships to move:")
            for label, count in m2m:
                self.stdout.write(f"  - {label}: {count}")
        if plan["fk"]:
            self.stdout.write("\nReferences to re-point:")
            for label, field, count in plan["fk"]:
                self.stdout.write(f"  - {label}.{field}: {count}")
        if plan["fields"]:
            self.stdout.write("\nFields to backfill on target:")
            for name, value in plan["fields"]:
                self.stdout.write(f"  - {name}: {value!r}")
        if not (plan["emails"] or m2m or plan["fk"] or plan["fields"]):
            self.stdout.write("\nNothing to merge.")

    # ----------------------------------------------------------------- apply
    def _apply(self, source, target, plan):
        # 1. EmailAddress: move each; skip/merge duplicates with the same verified
        #    email on the target; keep exactly one primary.
        target_emails = set(
            EmailAddress.objects.filter(user=target).values_list("email", flat=True)
        )
        target_has_primary = EmailAddress.objects.filter(
            user=target, primary=True
        ).exists()
        for ea in EmailAddress.objects.filter(user=source):
            if ea.email in target_emails or (
                ea.verified
                and EmailAddress.objects.filter(
                    user=target, email=ea.email, verified=True
                ).exists()
            ):
                ea.delete()
                continue
            ea.user = target
            if ea.primary:
                ea.primary = not target_has_primary
                target_has_primary = target_has_primary or ea.primary
            ea.save(update_fields=["user", "primary"])
            target_emails.add(ea.email)

        # 2. Groups and permissions: copy each membership onto the target.
        for through in (User.groups.through, User.user_permissions.through):
            user_fk = through._meta.get_field("user")
            related_fk = next(
                f for f in through._meta.fields if f is not user_fk and f.is_relation
            )
            for row in through.objects.filter(user_id=source.pk):
                key = {related_fk.name: getattr(row, related_fk.name)}
                if not through.objects.filter(
                    user_id=target.pk, **{related_fk.name: key[related_fk.name]}
                ).exists():
                    through.objects.create(user_id=target.pk, **key)

        # 3. All FK/OneToOne references.
        for label, name, _count in plan["fk"]:
            model = apps.get_model(label)
            model.objects.filter(**{name: source}).update(**{name: target})

        # 4. Backfill empty name fields on target.
        if plan["fields"]:
            User.objects.filter(pk=target.pk).update(
                **{name: value for name, value in plan["fields"]}
            )

        # 5. Remove the source account (references already re-pointed/cleared).
        source.delete()
