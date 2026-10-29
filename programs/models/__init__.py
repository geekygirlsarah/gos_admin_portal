from django.utils import timezone

from organizations.models import OrganizationManager, OrganizationQuerySet

from .adults import (
    CLEARANCE_VALIDITY_YEARS,
    Adult,
    AdultStudentRelationship,
    BackgroundCheck,
    BackgroundCheckType,
    _adult_photo_upload_to,
)
from .agreements import (
    MentorAgreement,
    MentorAgreementAcceptance,
    MentorAgreementSubmission,
    _agreement_submission_upload_to,
    _mentor_agreement_upload_to,
)
from .communication import EmailDeliveryEvent
from .enrollments import Enrollment
from .fields import (
    EncryptedCharField,
    EncryptedFieldFile,
    EncryptedFileDescriptor,
    EncryptedFileField,
    EncryptedTextField,
    _get_legacy_fernet,
    get_fernet,
    sync_linked_user,
)
from .finances import (
    Fee,
    FeeAssignment,
    Payment,
    SlidingScale,
    SlidingScaleSettings,
    TaxForm,
    _tax_form_upload_to,
)
from .geocoding import AddressGeocode
from .groups import Crew, SubTeam, Team
from .permissions import RolePermission
from .programs import (
    Program,
    ProgramDocument,
    ProgramFeature,
    ProgramQuerySet,
    _program_document_upload_to,
)
from .schools import RaceEthnicity, School, SchoolDistrict
from .students import (
    Student,
    StudentDocument,
    _student_document_upload_to,
    _student_photo_upload_to,
)

__all__ = [
    "AddressGeocode",
    "Adult",
    "AdultStudentRelationship",
    "BackgroundCheck",
    "BackgroundCheckType",
    "CLEARANCE_VALIDITY_YEARS",
    "Crew",
    "EmailDeliveryEvent",
    "EncryptedCharField",
    "EncryptedFieldFile",
    "EncryptedFileDescriptor",
    "EncryptedFileField",
    "EncryptedTextField",
    "Enrollment",
    "Fee",
    "FeeAssignment",
    "MentorAgreement",
    "MentorAgreementAcceptance",
    "MentorAgreementSubmission",
    "OrganizationManager",
    "OrganizationQuerySet",
    "Payment",
    "Program",
    "ProgramDocument",
    "ProgramFeature",
    "ProgramQuerySet",
    "RaceEthnicity",
    "RolePermission",
    "School",
    "SchoolDistrict",
    "SlidingScale",
    "SlidingScaleSettings",
    "Student",
    "StudentDocument",
    "SubTeam",
    "TaxForm",
    "Team",
    "_agreement_submission_upload_to",
    "_adult_photo_upload_to",
    "_get_legacy_fernet",
    "_mentor_agreement_upload_to",
    "_program_document_upload_to",
    "_student_document_upload_to",
    "_student_photo_upload_to",
    "_tax_form_upload_to",
    "get_fernet",
    "sync_linked_user",
    "timezone",
]
