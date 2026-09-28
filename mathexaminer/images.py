"""Turn uploaded photos / PDFs into compact JPEG pages for the vision model."""

from __future__ import annotations

import io
import warnings

from PIL import Image, ImageOps

MAX_SIDE_PX = 1600
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_PAGES = 4
JPEG_QUALITY = 85

# Guards against decompression bombs. Pillow only warns between 1x and 2x this value, so
# to_jpeg_pages turns that warning into a rejection too.
Image.MAX_IMAGE_PIXELS = 60_000_000
PDF_MAX_SCALE = 2.0


class ImageError(ValueError):
    """The upload cannot be turned into images. The message is safe to show to users."""


def is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def _to_jpeg(img: Image.Image) -> bytes:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        img = background
    else:
        img = img.convert("RGB")
    if max(img.size) > MAX_SIDE_PX:
        img.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue()


def _render_page(page) -> Image.Image:
    # Scale from the page's own size so the bitmap is never larger than we keep: a tiny PDF can
    # declare a 200-inch page, which at a fixed 2x would need gigabytes before any downscaling.
    width, height = page.get_size()
    scale = min(PDF_MAX_SCALE, MAX_SIDE_PX / max(width, height, 1))
    return page.render(scale=scale).to_pil()


def _render_pdf(data: bytes, max_pages: int) -> list[bytes]:
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(data)
        count = len(pdf)
        if count == 0:
            raise ImageError("That PDF has no pages.")
        if count > max_pages:
            raise ImageError(
                f"That PDF has {count} pages; the limit is {max_pages}. "
                "Split it or upload only the relevant pages."
            )
        return [_to_jpeg(_render_page(pdf[i])) for i in range(count)]
    except ImageError:
        raise
    except Exception as exc:
        raise ImageError(
            "Could not read that PDF (it may be password-protected or corrupt). "
            "Try a photo or screenshot instead."
        ) from exc


def to_jpeg_pages(data: bytes, max_pages: int = MAX_PAGES) -> list[bytes]:
    """Convert an image or PDF (detected from its bytes, not its file name) to JPEG pages."""
    if not data:
        raise ImageError("The file is empty. Please upload it again.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImageError(f"File is too large (limit {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")
    if is_pdf(data):
        return _render_pdf(data, max_pages)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data))
            img.load()
        img = ImageOps.exif_transpose(img)  # phone photos are often stored rotated
        return [_to_jpeg(img)]
    except Exception as exc:
        raise ImageError("That file is not a readable image. Use PNG, JPG, WEBP or PDF.") from exc


def files_to_jpeg_pages(blobs: list[bytes], max_pages: int = MAX_PAGES) -> list[bytes]:
    """Convert several uploads to one flat page list, capped at ``max_pages`` in total."""
    pages: list[bytes] = []
    for blob in blobs:
        remaining = max_pages - len(pages)
        if remaining <= 0:
            raise ImageError(f"Too many pages. The limit is {max_pages} pages per submission.")
        pages.extend(to_jpeg_pages(blob, max_pages=remaining))
    if not pages:
        raise ImageError("Please upload at least one file.")
    return pages
