from dataclasses import dataclass, field
from io import BytesIO
from struct import error as StructError

from fastapi import HTTPException, Request
from PIL import Image
from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header
from starlette.concurrency import run_in_threadpool

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
MAX_PART_HEADERS = 8 * 1024
MAX_REQUEST_BYTES = MAX_IMAGE_BYTES + 64 * 1024

MEDIA_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


@dataclass(frozen=True, slots=True)
class ImageUpload:
    content: bytes = field(repr=False)
    media_type: str


def invalid_multipart() -> HTTPException:
    return HTTPException(status_code=422, detail="invalid_image_multipart")


class ImageMultipart:
    def __init__(self, boundary: bytes) -> None:
        self.content = bytearray()
        self.header_name = bytearray()
        self.header_value = bytearray()
        self.disposition: bytes | None = None
        self.header_bytes = 0
        self.has_part = False
        self.complete = False
        self.parser = MultipartParser(
            boundary,
            {
                "on_part_begin": self.begin_part,
                "on_header_field": self.read_header_name,
                "on_header_value": self.read_header_value,
                "on_header_end": self.end_header,
                "on_headers_finished": self.finish_headers,
                "on_part_data": self.read_content,
                "on_end": self.finish,
            },
        )

    def begin_part(self) -> None:
        if self.has_part:
            raise invalid_multipart()
        self.has_part = True

    def append_header(
        self, buffer: bytearray, data: bytes, start: int, end: int
    ) -> None:
        self.header_bytes += end - start
        if self.header_bytes > MAX_PART_HEADERS:
            raise invalid_multipart()
        buffer.extend(data[start:end])

    def read_header_name(self, data: bytes, start: int, end: int) -> None:
        self.append_header(self.header_name, data, start, end)

    def read_header_value(self, data: bytes, start: int, end: int) -> None:
        self.append_header(self.header_value, data, start, end)

    def end_header(self) -> None:
        if self.header_name.lower() == b"content-disposition":
            if self.disposition is not None:
                raise invalid_multipart()
            self.disposition = bytes(self.header_value)
        self.header_name.clear()
        self.header_value.clear()

    def finish_headers(self) -> None:
        disposition, params = parse_options_header(self.disposition)
        if (
            disposition != b"form-data"
            or params.get(b"name") != b"image"
            or not params.get(b"filename")
        ):
            raise invalid_multipart()

    def read_content(self, data: bytes, start: int, end: int) -> None:
        if len(self.content) + end - start > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="image_too_large")
        self.content.extend(data[start:end])

    def finish(self) -> None:
        self.complete = True


async def read_image_bytes(request: Request) -> bytes:
    media_type, params = parse_options_header(request.headers.get("content-type"))
    if media_type != b"multipart/form-data":
        raise HTTPException(status_code=415, detail="multipart_required")
    boundary = params.get(b"boundary")
    if not boundary or len(boundary) > 70:
        raise invalid_multipart()
    upload = ImageMultipart(boundary)
    request_bytes = 0
    try:
        async for chunk in request.stream():
            request_bytes += len(chunk)
            if request_bytes > MAX_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="image_too_large")
            upload.parser.write(chunk)
        upload.parser.finalize()
    except MultipartParseError:
        raise invalid_multipart() from None
    if not upload.complete or not upload.has_part or not upload.content:
        raise invalid_multipart()
    return bytes(upload.content)


def detect_format(content: bytes) -> str:
    if content.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "WEBP"
    raise HTTPException(status_code=415, detail="unsupported_image_format")


def validate_image(content: bytes) -> ImageUpload:
    image_format = detect_format(content)
    try:
        buffer = BytesIO(content)
        with Image.open(buffer, formats=[image_format]) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise HTTPException(
                    status_code=413, detail="image_pixel_limit_exceeded"
                )
            if getattr(image, "n_frames", 1) != 1:
                raise HTTPException(
                    status_code=415, detail="animated_image_unsupported"
                )
            image.verify()
        buffer.seek(0)
        with Image.open(buffer, formats=[image_format]) as image:
            image.load()
    except Image.DecompressionBombError:
        raise HTTPException(
            status_code=413, detail="image_pixel_limit_exceeded"
        ) from None
    except (OSError, SyntaxError, ValueError, EOFError, StructError):
        raise HTTPException(status_code=422, detail="invalid_image") from None
    return ImageUpload(content=content, media_type=MEDIA_TYPES[image_format])


async def read_image_upload(request: Request) -> ImageUpload:
    content = await read_image_bytes(request)
    return await run_in_threadpool(validate_image, content)
