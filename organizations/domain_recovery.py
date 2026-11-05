import hashlib
import hmac
import time
from functools import wraps

from django.db import IntegrityError, OperationalError, connection, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from audit.events import AuditEvent
from audit.service import log_event
from organizations.domain_utils import (
    LEGACY_ORGANIZATION_HOSTNAMES,
    get_legacy_custom_hostname,
    is_momentum_hostname,
    normalize_domain_hostname,
)
from organizations.domain_verification import verify_domain_txt
from organizations.models import (
    Organization,
    OrganizationDomain,
    OrganizationDomainRecovery,
)


class RecoveryError(Exception):
    pass


def _retry_sqlite_lock(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        for attempt in range(5):
            try:
                return func(*args, **kwargs)
            except OperationalError as exc:
                if (
                    connection.vendor != "sqlite"
                    or "locked" not in str(exc).lower()
                    or attempt == 4
                ):
                    raise
                time.sleep(0.02 * (2**attempt))

    return wrapped


def _token_digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _legacy_hostnames(settings):
    if not isinstance(settings, dict):
        return set()
    hostnames = set()
    for key in ("custom_domain", "domain"):
        value = settings.get(key)
        if isinstance(value, dict):
            value = {**value, "enabled": True}
        hostname = get_legacy_custom_hostname({key: value})
        if hostname:
            hostnames.add(hostname)
    return hostnames


def _lock_legacy_claims():
    if connection.vendor == "postgresql":
        table = connection.ops.quote_name(Organization._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(f"LOCK TABLE {table} IN SHARE MODE")
    elif connection.vendor != "sqlite":
        raise RecoveryError(
            "Recovery approval is unavailable for this database backend."
        )


def _eligible(domain, organization, recovery=None, *, lock_organizations=False):
    if (
        domain.organization_id == organization.pk
        or domain.status != OrganizationDomain.STATUS_PENDING_VERIFICATION
        or domain.dns_verified_at is not None
        or domain.activated_at is not None
        or domain.activated_by_id is not None
        or domain.hostname in LEGACY_ORGANIZATION_HOSTNAMES
        or is_momentum_hostname(domain.hostname)
    ):
        return False
    if recovery and (
        recovery.status != OrganizationDomainRecovery.STATUS_PENDING
        or recovery.hostname != domain.hostname
        or recovery.original_organization_id != domain.organization_id
        or not hmac.compare_digest(
            recovery.original_token_digest, _token_digest(domain.verification_token)
        )
    ):
        return False
    organizations = Organization.objects.order_by("pk")
    if lock_organizations:
        _lock_legacy_claims()
        organizations = organizations.select_for_update()
    organizations = list(organizations.only("pk", "settings", "is_active"))
    if not any(org.pk == organization.pk and org.is_active for org in organizations):
        return False
    return not any(
        domain.hostname in _legacy_hostnames(org.settings) for org in organizations
    )


def _audit(request, organization, notes, before=None, after=None):
    if (
        log_event(
            request=request,
            event=AuditEvent.ORGANIZATION_UPDATED,
            resource=organization,
            notes=notes,
            before=before,
            after=after,
        )
        is None
    ):
        raise RecoveryError("The recovery action could not be audited.")


def request_recovery(request, organization, hostname):
    hostname = normalize_domain_hostname(hostname)
    with transaction.atomic():
        domain = (
            OrganizationDomain.objects.select_for_update()
            .filter(hostname=hostname)
            .first()
        )
        if not domain or not _eligible(domain, organization):
            raise RecoveryError(
                "This hostname is not eligible for recovery. Contact Momentum support."
            )
        try:
            with transaction.atomic():
                recovery = OrganizationDomainRecovery.objects.create(
                    organization=organization,
                    domain=domain,
                    original_organization_id=domain.organization_id,
                    hostname=domain.hostname,
                    original_token_digest=_token_digest(domain.verification_token),
                    created_by=request.user,
                )
        except IntegrityError as exc:
            raise RecoveryError(
                "A recovery request for this hostname is already pending."
            ) from exc
        _audit(
            request, organization, f"Requested recovery of portal hostname {hostname}."
        )
        return recovery


def _locked_recovery(organization, recovery_id):
    try:
        recovery_id = int(recovery_id)
    except (TypeError, ValueError):
        raise Http404("Recovery request not found.")
    if not 0 < recovery_id <= 9223372036854775807:
        raise Http404("Recovery request not found.")
    reference = get_object_or_404(
        OrganizationDomainRecovery.objects.for_organization(organization),
        pk=recovery_id,
    )
    domain = get_object_or_404(
        OrganizationDomain.objects.select_for_update(), pk=reference.domain_id
    )
    recovery = get_object_or_404(
        OrganizationDomainRecovery.objects.for_organization(
            organization
        ).select_for_update(),
        pk=reference.pk,
        domain=domain,
    )
    return domain, recovery


def verify_recovery(request, organization, recovery_id):
    with transaction.atomic():
        domain, recovery = _locked_recovery(organization, recovery_id)
        if not _eligible(domain, organization, recovery):
            raise RecoveryError("This recovery request is no longer eligible.")
        if not verify_domain_txt(recovery):
            raise RecoveryError(
                "The matching TXT record could not be found. Try again after DNS updates."
            )
        recovery.dns_verified_at = timezone.now()
        recovery.save(update_fields=["dns_verified_at", "updated_at"])
        _audit(
            request,
            organization,
            f"Verified recovery DNS proof for portal hostname {domain.hostname}.",
        )


def revoke_recovery(request, organization, recovery_id):
    with transaction.atomic():
        domain, recovery = _locked_recovery(organization, recovery_id)
        if recovery.status != OrganizationDomainRecovery.STATUS_PENDING:
            raise RecoveryError("This recovery request is no longer pending.")
        recovery.status = OrganizationDomainRecovery.STATUS_REVOKED
        recovery.save(update_fields=["status", "updated_at"])
        _audit(
            request,
            organization,
            f"Revoked recovery request for portal hostname {domain.hostname}.",
        )


@_retry_sqlite_lock
def approve_recovery(request, organization, recovery_id):
    with transaction.atomic():
        domain, recovery = _locked_recovery(organization, recovery_id)
        if (
            not organization.is_active
            or not recovery.dns_verified_at
            or not _eligible(domain, organization, recovery, lock_organizations=True)
        ):
            raise RecoveryError(
                "This recovery request is no longer eligible for approval."
            )
        if not verify_domain_txt(recovery):
            raise RecoveryError("The recovery TXT proof no longer matches.")
        now = timezone.now()
        old_organization_id = domain.organization_id
        # Identity changes are permitted only through this locked, audited transition.
        OrganizationDomain.objects.filter(pk=domain.pk).update(
            organization=organization,
            verification_token=recovery.verification_token,
            status=OrganizationDomain.STATUS_DNS_VERIFIED,
            dns_verified_at=now,
            activated_at=None,
            activated_by=None,
            updated_at=now,
        )
        recovery.status = OrganizationDomainRecovery.STATUS_APPROVED
        recovery.approved_by = request.user
        recovery.approved_at = now
        recovery.save(
            update_fields=["status", "approved_by", "approved_at", "updated_at"]
        )
        _audit(
            request,
            organization,
            f"Approved recovery of portal hostname {domain.hostname}; activation remains separate.",
            before={"organization_id": old_organization_id},
            after={
                "organization_id": organization.pk,
                "status": OrganizationDomain.STATUS_DNS_VERIFIED,
            },
        )
