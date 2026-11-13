"""Validate and normalize public listing photos; never preserve original metadata."""
import base64
import binascii
import io
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_INPUT = 2 * 1024 * 1024
MAX_PIXELS = 16_000_000

def normalize_photo(encoded: str) -> bytes:
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Invalid photo encoding") from None
    if not raw or len(raw) > MAX_INPUT:
        raise ValueError("Choose an image no larger than 2 MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw), formats=["JPEG", "PNG", "WEBP"]) as image:
                if image.width * image.height > MAX_PIXELS or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Choose a still image of at most 16 megapixels")
                image.load()
                normalized = ImageOps.exif_transpose(image).convert("RGB")
                normalized.thumbnail((1600, 1600))
                out = io.BytesIO()
                normalized.save(out, format="JPEG", quality=80, optimize=True)
                if out.tell() > 1_000_000:
                    raise ValueError("Photo is too large after compression")
                return out.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning, SyntaxError):
        raise ValueError("Use a valid JPEG, PNG or WebP photo") from None
