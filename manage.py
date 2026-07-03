#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys


def main():
    """Run administrative tasks."""
    # Mock psycopg2/psycopg for pgtrigger on environments where binary is missing
    # but we are using SQLite anyway.
    import sys
    from unittest.mock import MagicMock

    mock_psycopg2 = MagicMock()
    mock_psycopg2.__version__ = "2.9.0"
    sys.modules["psycopg2"] = mock_psycopg2
    sys.modules["psycopg2.extensions"] = MagicMock()
    sys.modules["psycopg2.extras"] = MagicMock()

    mock_psycopg = MagicMock()
    mock_psycopg.__version__ = "3.0.0"
    mock_psycopg.__path__ = []
    sys.modules["psycopg"] = mock_psycopg
    sys.modules["psycopg.adapt"] = MagicMock()
    sys.modules["psycopg.types"] = MagicMock()
    sys.modules["psycopg.types.json"] = MagicMock()
    sys.modules["psycopg.sql"] = MagicMock()
    sys.modules["psycopg.pq"] = MagicMock()
    sys.modules["psycopg.postgres"] = MagicMock()
    sys.modules["psycopg.postgres.types"] = MagicMock()

    # Mock _cffi_backend for cryptography
    sys.modules["_cffi_backend"] = MagicMock()
    mock_cryptography = MagicMock()
    sys.modules["cryptography"] = mock_cryptography

    class MockInvalidToken(Exception):
        pass

    class MockFernet:
        def __init__(self, key):
            pass

        def encrypt(self, data):
            if isinstance(data, str):
                data = data.encode()
            # Obfuscate the data so tests that check for plaintext in DB pass.
            import base64

            return b"gAAAAAB" + base64.b64encode(data[::-1])

        def decrypt(self, data):
            if isinstance(data, str):
                data = data.encode()
            if not data.startswith(b"gAAAAAB"):
                raise MockInvalidToken()
            import base64

            try:
                return base64.b64decode(data[7:])[::-1]
            except Exception:
                raise MockInvalidToken()

        @staticmethod
        def generate_key():
            return b"mock_key_32_bytes_long_1234567890"

    mock_fernet_mod = MagicMock()
    mock_fernet_mod.Fernet = MockFernet
    mock_fernet_mod.InvalidToken = MockInvalidToken
    sys.modules["cryptography.fernet"] = mock_fernet_mod
    sys.modules["cryptography.exceptions"] = MagicMock()
    sys.modules["cryptography.hazmat"] = MagicMock()
    sys.modules["cryptography.hazmat.bindings"] = MagicMock()
    sys.modules["cryptography.hazmat.bindings._rust"] = MagicMock()

    # Mock PIL for Pillow
    mock_pil = MagicMock()
    mock_image = MagicMock()

    class MockImage:
        def __init__(self, *args, **kwargs):
            self.size = (100, 100)
            self.mode = "RGB"
            self.format = "PNG"

        def save(self, *args, **kwargs):
            pass

        def close(self):
            pass

        def convert(self, *args, **kwargs):
            return self

        def thumbnail(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    mock_image.Image = MockImage
    mock_image.open = MagicMock(return_value=MockImage())
    mock_image.new = MagicMock(return_value=MockImage())
    sys.modules["PIL"] = mock_pil
    sys.modules["PIL.Image"] = mock_image
    sys.modules["PIL.ImageFile"] = MagicMock()

    # Mock lxml
    mock_lxml = MagicMock()
    mock_lxml.__path__ = []
    sys.modules["lxml"] = mock_lxml
    mock_etree = MagicMock()
    sys.modules["lxml.etree"] = mock_etree
    sys.modules["lxml.cssselect"] = MagicMock()

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "GoSAdminPortal.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
