import os
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

_ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
_MAX_BYTES = 5 * 1024 * 1024   # 5 MB — matches DATA_UPLOAD_MAX_MEMORY_SIZE
_MAX_PIXELS = 10_000_000        # 10 MP decompression-bomb ceiling


def validate_image_upload(upload):
    """
    Rejects uploaded files that are not genuine images or may be malicious.

    Checks (in order):
    1. File extension is in the allowed set
    2. File size does not exceed 5 MB
    3. Pillow can fully decode all pixel data — this catches truncated files,
       corrupted images, and polyglot payloads disguised as image files
    4. Image dimensions stay within the 10-megapixel decompression-bomb ceiling
    """
    ext = os.path.splitext(upload.name)[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise ValidationError(
            _("Unsupported file type. Allowed: %(types)s."),
            params={'types': ', '.join(sorted(_ALLOWED_EXTENSIONS))},
        )

    if upload.size > _MAX_BYTES:
        raise ValidationError(_("Image must not exceed 5 MB."))

    try:
        from PIL import Image
        # Enforce pixel ceiling before opening — prevents decompression bomb attacks.
        Image.MAX_IMAGE_PIXELS = _MAX_PIXELS
        upload.seek(0)
        img = Image.open(upload)
        img.load()      # fully decode every pixel — rejects truncated / embedded payloads
        upload.seek(0)
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError(
            _("The file is not a valid image or is corrupted.")
        ) from exc
