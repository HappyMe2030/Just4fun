"""Resize/compress uploaded images so they're sized for mobile and small
laptop screens instead of storing whatever the original upload was."""
import io

from PIL import Image, ImageOps

MAX_DIMENSION = 1024  # px, long edge — plenty for phone/laptop viewing
JPEG_QUALITY = 82


class InvalidImage(Exception):
    pass


def process_image(file_storage):
    """Takes a werkzeug FileStorage, returns (bytes, mimetype) resized to
    fit within MAX_DIMENSION on the long edge. Raises InvalidImage if the
    upload isn't a readable image."""
    try:
        img = Image.open(file_storage.stream)
        img.load()
    except Exception:
        raise InvalidImage("That file doesn't look like a valid image.")

    # respect the camera's orientation tag instead of saving it sideways
    img = ImageOps.exif_transpose(img)

    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    )

    if img.width > MAX_DIMENSION or img.height > MAX_DIMENSION:
        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

    buffer = io.BytesIO()
    if has_alpha:
        img = img.convert("RGBA")
        img.save(buffer, format="PNG", optimize=True)
        mime = "image/png"
    else:
        img = img.convert("RGB")
        img.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        mime = "image/jpeg"

    return buffer.getvalue(), mime
