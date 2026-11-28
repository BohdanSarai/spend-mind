from io import BytesIO
from struct import pack
from zlib import crc32

import pytest
from fastapi import HTTPException, Request
from PIL import Image

from spend_mind.routers.image_upload import (
    MAX_IMAGE_BYTES,
    read_image_bytes,
    validate_image,
)


@pytest.mark.parametrize(
    ("image_format", "media_type"),
    [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")],
)
def test_validates_actual_image_format(image_format: str, media_type: str) -> None:
    buffer = BytesIO()
    with Image.new("RGB", (8, 8)) as image:
        image.save(buffer, format=image_format)
    validated = validate_image(buffer.getvalue())
    assert validated.media_type == media_type
    assert validated.content == buffer.getvalue()


@pytest.mark.parametrize(
    ("content", "status"),
    [(b"<html>not an image</html>", 415), (b"\xff\xd8\xffoops", 422)],
)
def test_rejects_nonimage_and_corrupted_image(content: bytes, status: int) -> None:
    with pytest.raises(HTTPException) as caught:
        validate_image(content)
    assert caught.value.status_code == status


def test_rejects_excessive_dimensions_before_decoding_pixels() -> None:
    header = b"IHDR" + pack(">IIBBBBB", 5001, 5000, 8, 2, 0, 0, 0)
    data = b"\x89PNG\r\n\x1a\n" + pack(">I", 13) + header + pack(">I", crc32(header))
    # Provide an IDAT header so Pillow can identify the image without any pixel data.
    data += pack(">I", 1) + b"IDAT"
    with pytest.raises(HTTPException) as caught:
        validate_image(data)
    assert caught.value.status_code == 413
    assert caught.value.detail == "image_pixel_limit_exceeded"


def multipart_request(
    data: bytes, *, filename: bytes = b"x.png", ending: bytes = b"\r\n--test--\r\n"
) -> Request:
    stem = (
        b'--test\r\nContent-Disposition: form-data; name="image"; filename="'
        + filename
        + b'"\r\nContent-Type: text/plain\r\n\r\n'
    )
    chunks = iter([stem[:25], stem[25:], data, ending])

    async def receive() -> dict:
        chunk = next(chunks, None)
        return {
            "type": "http.request",
            "body": chunk or b"",
            "more_body": chunk is not None,
        }

    return Request(
        {
            "type": "http",
            "headers": [(b"content-type", b"multipart/form-data; boundary=test")],
        },
        receive,
    )


@pytest.mark.asyncio
async def test_streams_image_to_memory_at_exact_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_disk_spooling(*args: object, **kwargs: object) -> None:
        pytest.fail("Multipart upload must stay in memory")

    monkeypatch.setattr(
        "starlette.formparsers.SpooledTemporaryFile", reject_disk_spooling
    )
    data = b"x" * MAX_IMAGE_BYTES
    assert await read_image_bytes(multipart_request(data)) == data
    with pytest.raises(HTTPException) as caught:
        await read_image_bytes(multipart_request(data + b"x"))
    assert caught.value.status_code == 413


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ending",
    [
        b"",
        b'\r\n--test\r\nContent-Disposition: form-data; name="extra"\r\n\r\nX\r\n--test--\r\n',
    ],
)
async def test_rejects_unterminated_upload_or_extra_parts(ending: bytes) -> None:
    with pytest.raises(HTTPException) as caught:
        await read_image_bytes(multipart_request(b"data", ending=ending))
    assert caught.value.status_code == 422
