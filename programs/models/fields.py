import base64
import logging
from io import BytesIO

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models
from django.db.models.fields.files import FieldFile
from PIL import ImageFile

logger = logging.getLogger(__name__)


def sync_linked_user(profile, login_enabled):
    """Copy a profile's display name and login-active state onto its linked
    User account (if any).

    Shared by ``Student.save`` and ``Adult.save``. Returns True if the linked
    User was updated.
    """
    user = profile.user
    if not user:
        return False
    target_first = profile.preferred_first_name or profile.legal_first_name
    changed = False
    if user.first_name != target_first:
        user.first_name = target_first
        changed = True
    if user.last_name != profile.last_name:
        user.last_name = profile.last_name
        changed = True
    if user.is_active != login_enabled:
        user.is_active = login_enabled
        changed = True
    if changed:
        user.save()
    return changed


def get_fernet():
    key = getattr(settings, "FILE_ENCRYPTION_KEY", None)
    if not key:
        if getattr(settings, "DEBUG", False):
            # In DEBUG mode, derive a key from SECRET_KEY for local dev/test convenience.
            # This is NOT secure for production use.
            key = base64.urlsafe_b64encode(
                settings.SECRET_KEY[:32].encode().ljust(32, b"\0")
            )
        else:
            raise RuntimeError(
                "FILE_ENCRYPTION_KEY is not configured. "
                "Set FILE_ENCRYPTION_KEY environment variable. "
                'Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


def _get_legacy_fernet():
    """Return a Fernet instance using the old SECRET_KEY-derived key.

    Before ``FILE_ENCRYPTION_KEY`` was introduced, ``get_fernet()`` fell
    back to a key derived from ``SECRET_KEY``. Records encrypted before
    the transition can only be decrypted with that legacy key.
    """
    key = base64.urlsafe_b64encode(settings.SECRET_KEY[:32].encode().ljust(32, b"\0"))
    return Fernet(key)


class EncryptedFieldFile(FieldFile):
    def save(self, name, content, save=True):
        if not getattr(content, "_encrypted", False):
            fernet = get_fernet()
            if hasattr(content, "seek"):
                content.seek(0)
            data = content.read()
            try:
                fernet.decrypt(data)
                encrypted = data
            except (InvalidToken, TypeError):
                encrypted = fernet.encrypt(data)
            content = ContentFile(encrypted, name=name)
            content._encrypted = True
        return super().save(name, content, save=save)


class EncryptedFileField(models.FileField):
    """
    A FileField that transparently encrypts file content on save (via pre_save)
    and decrypts on read (via EncryptedFileDescriptor). Uses Fernet symmetric
    encryption with the project's FILE_ENCRYPTION_KEY setting.
    """

    attr_class = EncryptedFieldFile

    def pre_save(self, model_instance, add):
        file = getattr(model_instance, self.attname)
        if file and not file._committed and not getattr(file, "_encrypted", False):
            fernet = get_fernet()
            if hasattr(file, "seek"):
                file.seek(0)
            data = file.read()
            try:
                fernet.decrypt(data)
                encrypted = data
            except (InvalidToken, TypeError):
                encrypted = fernet.encrypt(data)
            file.file = ContentFile(encrypted, name=file.name)
            file.file._encrypted = True
            file._encrypted = True
        return super().pre_save(model_instance, add)

    def contribute_to_class(self, cls, name, private_only=False):
        super().contribute_to_class(cls, name)
        # Patch the descriptor to decrypt when accessed
        setattr(cls, self.name, EncryptedFileDescriptor(getattr(cls, self.name)))


class EncryptedTextField(models.TextField):
    def get_prep_value(self, value):
        if value is None or value == "":
            return None
        fernet = get_fernet()
        try:
            fernet.decrypt(value.encode())
            return value  # already encrypted
        except (InvalidToken, UnicodeEncodeError):
            pass
        return fernet.encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        fernet = get_fernet()
        try:
            return fernet.decrypt(value.encode()).decode()
        except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError):
            # If decryption fails, return original (might be already decrypted or not encrypted)
            return value


class EncryptedCharField(models.CharField):
    def get_prep_value(self, value):
        if value is None or value == "":
            return None
        fernet = get_fernet()
        try:
            fernet.decrypt(value.encode())
            return value  # already encrypted
        except (InvalidToken, UnicodeEncodeError):
            pass
        return fernet.encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        fernet = get_fernet()
        try:
            return fernet.decrypt(value.encode()).decode()
        except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError):
            # If decryption fails, return original (might be already decrypted or not encrypted)
            return value


class EncryptedFileDescriptor:
    def __init__(self, original_field):
        self.original_field = original_field

    def __get__(self, instance, owner):
        if instance is None:
            return self
        file = self.original_field.__get__(instance, owner)
        if file and not hasattr(file, "_decrypted_file"):
            original_open = file.open

            def decrypted_open(mode="rb"):
                f = original_open(mode)
                if "b" not in mode:
                    return f
                try:
                    f.seek(0)
                    content = f.read()
                finally:
                    # The ciphertext is fully read; close the underlying
                    # storage handle so we don't hold file descriptors open for
                    # the lifetime of the instance. The decrypted bytes live in
                    # the returned BytesIO instead.
                    f.close()
                try:
                    fernet = get_fernet()
                    return BytesIO(fernet.decrypt(content))
                except InvalidToken:
                    # The stored bytes may be legacy plaintext; return a fresh
                    # handle positioned at the start so the caller can read the
                    # raw file.
                    f = original_open(mode)
                    f.seek(0)
                    return f

            file.open = decrypted_open
            file._decrypted_file = True
        return file

    def __set__(self, instance, value):
        self.original_field.__set__(instance, value)


# Make PIL more tolerant of malformed/truncated images (common after conversions/exports)
ImageFile.LOAD_TRUNCATED_IMAGES = True

# Register HEIC opener if available so PIL can decode .heic images
try:
    from pillow_heif import register_heif_opener  # type: ignore

    register_heif_opener()
except ImportError:
    # If pillow-heif isn't installed, we simply won't be able to open HEIC files.
    # The save() handler below will skip conversion in that case.
    pass
except Exception:
    logger.exception("Unexpected error registering HEIF opener")
