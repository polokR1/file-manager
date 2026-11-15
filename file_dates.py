"""Read creation dates from file metadata without decoding image pixels."""

from datetime import datetime, timedelta
import os
from pathlib import Path
import struct
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile
import zlib

from PIL import ExifTags, Image


IMAGE_FORMATS = {
    ".jpg": "JPEG", ".jpeg": "JPEG", ".jpe": "JPEG",
    ".tif": "TIFF", ".tiff": "TIFF",
    ".png": "PNG", ".webp": "WEBP",
}
OFFICE_EXTENSIONS = {".docx", ".xlsx", ".pptx"}
MAX_EXIF_BYTES = 1024 * 1024
MAX_OFFICE_BYTES = 128 * 1024 * 1024
MAX_CORE_BYTES = 64 * 1024
MAX_CONTAINER_CHUNKS = 4096
CREATED_PROPERTY = "{http://purl.org/dc/terms/}created"


def file_creation_date(info: os.stat_result) -> datetime | None:
    timestamp = getattr(info, "st_birthtime", None)
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(timestamp)
    except (OSError, ValueError, OverflowError):
        # Windows localtime may reject genuine pre-1970 creation timestamps.
        try:
            return datetime(1970, 1, 1) + timedelta(seconds=timestamp)
        except (ValueError, OverflowError):
            return None


def _png_exif(file, file_size: int) -> bytes | None:
    if file.read(8) != b"\x89PNG\r\n\x1a\n":
        return None
    for _ in range(MAX_CONTAINER_CHUNKS):
        header = file.read(8)
        if len(header) != 8:
            return None
        length, kind = struct.unpack(">I4s", header)
        next_chunk = file.tell() + length + 4
        if next_chunk > file_size or kind == b"IEND":
            return None
        if kind == b"eXIf":
            if length > MAX_EXIF_BYTES:
                return None
            contents = file.read(length)
            checksum = file.read(4)
            if len(checksum) == 4 and zlib.crc32(contents, zlib.crc32(kind)) == int.from_bytes(checksum, "big"):
                return contents
            return None
        file.seek(next_chunk)
    return None


def _webp_exif(file, file_size: int) -> bytes | None:
    header = file.read(12)
    if len(header) != 12:
        return None
    magic, length, form = struct.unpack("<4sI4s", header)
    end = length + 8
    if magic != b"RIFF" or form != b"WEBP" or not 12 <= end <= file_size:
        return None
    for _ in range(MAX_CONTAINER_CHUNKS):
        if file.tell() + 8 > end:
            return None
        header = file.read(8)
        if len(header) != 8:
            return None
        kind, length = struct.unpack("<4sI", header)
        next_chunk = file.tell() + length + (length % 2)
        if next_chunk > end:
            return None
        if kind == b"EXIF":
            return file.read(length) if length <= MAX_EXIF_BYTES else None
        file.seek(next_chunk)
    return None


def _capture_date(exif: Image.Exif) -> datetime | None:
    photo_tags = exif.get_ifd(ExifTags.IFD.Exif)
    for tag in (ExifTags.Base.DateTimeOriginal, ExifTags.Base.DateTimeDigitized):
        for value in (photo_tags.get(tag), exif.get(tag)):
            if isinstance(value, str):
                try:
                    return datetime.strptime(value.strip(" \0"), "%Y:%m:%d %H:%M:%S")
                except ValueError:
                    continue
    return None


def _image_created(path: Path, format_name: str, file_size: int) -> datetime | None:
    try:
        if format_name in {"PNG", "WEBP"}:
            with path.open("rb") as file:
                data = _png_exif(file, file_size) if format_name == "PNG" else _webp_exif(file, file_size)
            if not data:
                return None
            exif = Image.Exif()
            exif.load(data)
            return _capture_date(exif)
        with Image.open(path, formats=(format_name,)) as image:
            return _capture_date(image.getexif())
    except (OSError, EOFError, ValueError, TypeError, OverflowError, IndexError, KeyError,
            SyntaxError, struct.error, Image.DecompressionBombError):
        return None


def _office_created(path: Path, file_size: int) -> datetime | None:
    if file_size > MAX_OFFICE_BYTES:
        return None
    try:
        with ZipFile(path) as archive:
            member = archive.getinfo("docProps/core.xml")
            if member.file_size > MAX_CORE_BYTES:
                return None
            with archive.open(member) as properties:
                raw = properties.read(MAX_CORE_BYTES + 1)
        if len(raw) > MAX_CORE_BYTES:
            return None
        xml = raw.decode("utf-8-sig")
        if "<!DOCTYPE" in xml.upper():
            return None
        created = ElementTree.fromstring(xml).findtext(CREATED_PROPERTY)
        return datetime.fromisoformat(created.strip()) if created else None
    except (OSError, EOFError, ValueError, RuntimeError, KeyError, BadZipFile, ElementTree.ParseError, zlib.error):
        return None


def original_file_date(path: Path, info: os.stat_result) -> datetime | None:
    """Return capture/document creation metadata only, or None."""
    suffix = path.suffix.casefold()
    if suffix in IMAGE_FORMATS:
        return _image_created(path, IMAGE_FORMATS[suffix], info.st_size)
    if suffix in OFFICE_EXTENSIONS:
        return _office_created(path, info.st_size)
    return None
