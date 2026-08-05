from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from programs.models import SlidingScale, Student, TaxForm


@override_settings(FILE_ENCRYPTION_KEY="ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg=")
class EncryptedFileRepeatedOpenTests(TestCase):
    """Reproducers for EncryptedFileDescriptor.decrypted_open bugs:
    1. Opening the same FieldFile instance twice silently returns empty
       content the second time (the storage handle stays at EOF).
    2. The underlying storage handle is never closed after the decrypted
       read, leaking file descriptors.
    """

    def setUp(self):
        self.student = Student.objects.create(
            legal_first_name="Test",
            last_name="Student",
            personal_email="student@example.com",
        )
        self.sliding_scale = SlidingScale.objects.create(student=self.student)

    def _create_tax_form(self, content):
        return TaxForm.objects.create(
            sliding_scale=self.sliding_scale,
            file=SimpleUploadedFile("doc.pdf", content),
        )

    def test_repeated_opens_return_full_content(self):
        content = b"repeated open content"
        tax_form = self._create_tax_form(content)

        with tax_form.file.open("rb") as f:
            first = f.read()
        with tax_form.file.open("rb") as f:
            second = f.read()

        self.assertEqual(first, content)
        self.assertEqual(second, content)

    def test_open_closes_underlying_storage_handle(self):
        content = b"handle leak content"
        tax_form = self._create_tax_form(content)

        with tax_form.file.open("rb") as f:
            self.assertEqual(f.read(), content)

        self.assertTrue(tax_form.file.closed)

    def test_legacy_plaintext_file_readable_through_fallback(self):
        content = b"legacy plaintext not encrypted"
        tax_form = TaxForm.objects.create(sliding_scale=self.sliding_scale)
        tax_form.file.save("legacy.txt", ContentFile(content), save=True)

        with tax_form.file.open("rb") as f:
            self.assertEqual(f.read(), content)
