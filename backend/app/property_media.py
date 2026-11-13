"""Validate and normalize admin-uploaded public property photos."""
import base64
import binascii
import io
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD = 2 * 1024 * 1024
MAX_PIXELS = 12_000_000

def normalize_photo(encoded: str) -> bytes:
    try:
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > MAX_UPLOAD:
            raise ValueError("Photo must be at most 2 MB.")
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("Use a JPEG, PNG or WebP photo.")
            if image.width * image.height > MAX_PIXELS or getattr(image, "n_frames", 1) != 1:
                raise ValueError("Use a single photo up to 12 megapixels.")
            image.load()
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((1200, 1200))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=82, optimize=True)
            return output.getvalue()
    except (binascii.Error, UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError):
        raise ValueError("Invalid image. Use a valid JPEG, PNG or WebP photo.") from None
