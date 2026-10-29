"""Tests for shared file and upload path utilities."""

from django.test import SimpleTestCase

from programs.utils.files import (
    build_upload_path,
    sanitize_upload_filename,
    scoped_upload_path_builder,
)


class DummyInstance:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FileUtilsTests(SimpleTestCase):
    def test_sanitize_upload_filename(self):
        self.assertEqual(sanitize_upload_filename("hello world.pdf"), "hello_world.pdf")
        self.assertEqual(sanitize_upload_filename("test space!.pdf"), "test_space.pdf")
        self.assertEqual(sanitize_upload_filename(""), "")

    def test_build_upload_path_flat(self):
        path = build_upload_path("photos/students", filename="My Photo.JPG")
        self.assertEqual(path, "photos/students/My_Photo.JPG")

    def test_build_upload_path_with_id_attr(self):
        instance = DummyInstance(student_id=42)
        path = build_upload_path(
            "student_documents",
            instance=instance,
            filename="Form A.pdf",
            id_attr="student_id",
        )
        self.assertEqual(path, "student_documents/42/Form_A.pdf")

    def test_build_upload_path_with_missing_id_fallback(self):
        instance = DummyInstance(student_id=None)
        path = build_upload_path(
            "student_documents",
            instance=instance,
            filename="Form A.pdf",
            id_attr="student_id",
            default_id="unassigned",
        )
        self.assertEqual(path, "student_documents/unassigned/Form_A.pdf")

    def test_build_upload_path_direct_id(self):
        instance = DummyInstance(pk=99)
        path = build_upload_path(
            "agreements",
            instance=instance,
            filename="signed.pdf",
            id_attr="pk",
        )
        self.assertEqual(path, "agreements/99/signed.pdf")

    def test_scoped_upload_path_builder(self):
        builder = scoped_upload_path_builder("documents", id_attr="doc_id")
        instance = DummyInstance(doc_id="xyz123")
        path = builder(instance, "w9 form.pdf")
        self.assertEqual(path, "documents/xyz123/w9_form.pdf")
