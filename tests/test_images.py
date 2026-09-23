import io

import pytest
from conftest import make_image, make_pdf
from PIL import Image

from mathexaminer import images


def decode(jpeg: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(jpeg))
    img.load()
    return img


def test_png_becomes_jpeg():
    (page,) = images.to_jpeg_pages(make_image())
    assert decode(page).format == "JPEG"


def test_large_image_is_downscaled_keeping_aspect():
    (page,) = images.to_jpeg_pages(make_image(size=(4000, 2000)))
    w, h = decode(page).size
    assert max(w, h) == images.MAX_SIDE_PX
    assert abs(w / h - 2.0) < 0.02


def test_small_image_is_not_upscaled():
    (page,) = images.to_jpeg_pages(make_image(size=(300, 200)))
    assert decode(page).size == (300, 200)


def test_transparent_png_gets_white_background():
    (page,) = images.to_jpeg_pages(make_image(size=(50, 50), color=(0, 0, 0, 0), mode="RGBA"))
    r, g, b = decode(page).getpixel((25, 25))
    assert min(r, g, b) > 240


def test_exif_rotation_is_applied():
    img = Image.new("RGB", (200, 100), (10, 10, 10))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 CW when displayed -> portrait
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    (page,) = images.to_jpeg_pages(buf.getvalue())
    assert decode(page).size == (100, 200)


def test_pdf_pages_are_rendered():
    pages = images.to_jpeg_pages(make_pdf(pages=3))
    assert len(pages) == 3
    assert all(decode(p).format == "JPEG" for p in pages)


def test_pdf_over_page_limit_is_rejected_not_truncated():
    with pytest.raises(images.ImageError, match="limit"):
        images.to_jpeg_pages(make_pdf(pages=5), max_pages=4)


def test_pdf_detected_by_content_not_extension():
    assert images.is_pdf(make_pdf())
    assert not images.is_pdf(make_image())


@pytest.mark.parametrize("blob", [b"", b"not an image at all", b"%PDF-1.4 garbage"])
def test_bad_input_raises_friendly_error(blob):
    with pytest.raises(images.ImageError):
        images.to_jpeg_pages(blob)


def test_oversized_upload_rejected():
    with pytest.raises(images.ImageError, match="too large"):
        images.to_jpeg_pages(b"0" * (images.MAX_UPLOAD_BYTES + 1))


def test_multiple_files_share_one_page_budget():
    blobs = [make_image(), make_pdf(pages=3)]
    assert len(images.files_to_jpeg_pages(blobs, max_pages=4)) == 4
    with pytest.raises(images.ImageError):
        images.files_to_jpeg_pages(blobs + [make_image()], max_pages=4)


def test_no_files_rejected():
    with pytest.raises(images.ImageError):
        images.files_to_jpeg_pages([])
