"""Image normalization utilities for Student/Adult photo fields."""

from __future__ import annotations

import logging
import os
from io import BytesIO

from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)


def normalize_image_field(file_field, *, quality=85, log_prefix="image"):
    """Re-encode an uploaded ImageField as a normalized RGB JPEG, in place.

    - No-op when the field is empty or already committed (already-stored files).
    - Fixes EXIF orientation, converts to RGB, optimizes JPEG output.
    - Falls back to a HEIC/HEIF-aware path if the primary decode fails.
    - Never raises: any unexpected error is logged and the original file is left
      untouched so the surrounding ``Model.save()`` can proceed.

    Returns True if the field was rewritten, False otherwise.
    """
    if not file_field:
        return False
    try:
        from PIL import Image, ImageOps
    except ImportError:
        logger.debug("Pillow unavailable; skipping %s normalization", log_prefix)
        return False

    try:
        # Only process a newly assigned upload (uncommitted) or anything with an accessible file handle
        if getattr(file_field, "_committed", True) and not hasattr(file_field, "file"):
            return False
        try:
            f = getattr(file_field, "file", file_field)
            try:
                f.seek(0)
            except (AttributeError, IOError):
                pass
            img = Image.open(f)
            img.load()
            try:
                img = ImageOps.exif_transpose(img)
            except (AttributeError, TypeError, IndexError):
                pass
            if img.mode != "RGB":
                img = img.convert("RGB")
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=quality, optimize=True)
            buffer.seek(0)
            base, _ = os.path.splitext(file_field.name or "photo")
            new_name = f"{base}.jpg"
            file_field.save(new_name, ContentFile(buffer.read()), save=False)
            return True
        except Exception:
            # Fallback: legacy HEIC/HEIF detection and conversion
            try:
                name_lower = (file_field.name or "").lower()
                needs_convert_by_ext = name_lower.endswith(
                    ".heic"
                ) or name_lower.endswith(".heif")
                convert = needs_convert_by_ext
                if not convert:
                    try:
                        file_field.open("rb")
                        img_probe = Image.open(file_field)
                        fmt = (img_probe.format or "").upper()
                        img_probe.close()
                        if fmt in ("HEIC", "HEIF"):
                            convert = True
                    except Exception:
                        convert = needs_convert_by_ext
                    finally:
                        try:
                            file_field.close()
                        except (AttributeError, IOError):
                            pass
                if convert:
                    file_field.open("rb")
                    img = Image.open(file_field)
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    buffer = BytesIO()
                    img.save(buffer, format="JPEG", quality=quality, optimize=True)
                    buffer.seek(0)
                    base, _ = os.path.splitext(file_field.name or "photo")
                    new_name = f"{base}.jpg"
                    file_field.save(new_name, ContentFile(buffer.read()), save=False)
                    try:
                        file_field.close()
                    except (AttributeError, IOError):
                        pass
                    return True
            except Exception:
                logger.debug("%s normalization failed", log_prefix, exc_info=True)
    except Exception:
        logger.debug("Unexpected error in %s processing", log_prefix, exc_info=True)
    return False
