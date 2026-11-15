from datetime import datetime, timezone
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from zipfile import ZIP_DEFLATED, ZipFile
import zlib

from PIL import ExifTags, Image

from file_dates import file_creation_date, original_file_date


class OriginalFileDateTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def image(self, name, *, original=None, digitized=None):
        image = Image.new("RGB", (2, 2), "red")
        self.addCleanup(image.close)
        exif = image.getexif()
        exif[ExifTags.Base.DateTime] = "2030:11:12 13:14:15"
        nested_dates = {}
        if original is not None:
            nested_dates[ExifTags.Base.DateTimeOriginal] = original
        if digitized is not None:
            nested_dates[ExifTags.Base.DateTimeDigitized] = digitized
        if nested_dates:
            exif[ExifTags.IFD.Exif] = nested_dates
        path = self.root / name
        image.save(path, exif=exif.tobytes())
        return path

    def test_original_photo_date_has_priority_over_other_exif_tags(self):
        path = self.image("image.JPG", original="2017:03:04 05:06:07", digitized="2022:09:10 11:12:13")
        self.assertEqual(original_file_date(path, path.stat()), datetime(2017, 3, 4, 5, 6, 7))

    def test_digitized_is_used_when_original_capture_date_is_invalid(self):
        path = self.image("poster.tiff", original="2020:00:05 12:00:00", digitized="2004:01:02 03:04:05")
        self.assertEqual(original_file_date(path, path.stat()), datetime(2004, 1, 2, 3, 4, 5))

    def test_generic_exif_modify_date_is_not_the_original_date(self):
        path = self.image("image.webp")
        self.assertIsNone(original_file_date(path, path.stat()))

    def test_png_exif_after_pixel_data_is_read_without_loading_pixels(self):
        path = self.root / "late-exif.png"
        exif = Image.Exif()
        exif[ExifTags.IFD.Exif] = {ExifTags.Base.DateTimeOriginal: "2012:06:07 08:09:10"}

        def png_chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

        header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", header) +
                         png_chunk(b"IDAT", b"intentionally invalid raster") +
                         png_chunk(b"eXIf", exif.tobytes().removeprefix(b"Exif\0\0")) +
                         png_chunk(b"IEND", b""))
        self.assertEqual(original_file_date(path, path.stat()), datetime(2012, 6, 7, 8, 9, 10))

    def test_webp_has_embedded_photo_date(self):
        path = self.image("image.webp", original="2020:04:07 08:08:08")
        self.assertEqual(original_file_date(path, path.stat()), datetime(2020, 4, 7, 8, 8, 8))

    def test_non_image_filenames_do_not_get_filesystem_date_as_metadata(self):
        path = self.root / "document.txt"
        path.write_bytes(b"example")
        self.assertIsNone(original_file_date(path, path.stat()))

    def test_broken_image_header_produces_unknown_date(self):
        path = self.root / "image.jpg"
        path.write_bytes(b"\xff\xd8\xff\xe1\xff\xffExif\x00\x00SHORT")
        self.assertIsNone(original_file_date(path, path.stat()))

    def test_office_uses_created_not_modified_property(self):
        xml = b"""<coreProperties xmlns:dcterms="http://purl.org/dc/terms/">
          <dcterms:created>2010-01-02T23:30:00Z</dcterms:created>
          <dcterms:modified>2026-03-04T10:11:12Z</dcterms:modified>
        </coreProperties>"""
        for suffix in ("docx", "xlsx", "pptx"):
            with self.subTest(suffix=suffix):
                path = self.root / f"document.{suffix}"
                with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
                    archive.writestr("docProps/core.xml", xml)
                self.assertEqual(original_file_date(path, path.stat()),
                                 datetime(2010, 1, 2, 23, 30, tzinfo=timezone.utc))

    def test_unreasonably_large_office_metadata_is_skipped(self):
        path = self.root / "large-properties.docx"
        xml = (b'<coreProperties xmlns:dcterms="http://purl.org/dc/terms/">'
               b"<dcterms:created>2010-01-02T23:30:00Z</dcterms:created>" +
               b" " * (256 * 1024) + b"</coreProperties>")
        with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("docProps/core.xml", xml)
        self.assertIsNone(original_file_date(path, path.stat()))

    def test_office_rejects_document_type_declarations(self):
        path = self.root / "extra-entity.docx"
        xml = (b'<!DOCTYPE coreProperties [<!ENTITY date "2010-01-02T23:30:00Z">]>'
               b'<coreProperties xmlns:dcterms="http://purl.org/dc/terms/">'
               b"<dcterms:created>&date;</dcterms:created></coreProperties>")
        with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("docProps/core.xml", xml)
        self.assertIsNone(original_file_date(path, path.stat()))


class CreationDateTests(unittest.TestCase):
    def test_reads_birth_time_and_not_other_file_dates(self):
        first = datetime(2000, 6, 7, 8, 9, 10)
        info = SimpleNamespace(st_mtime=111, st_ctime=222, st_birthtime=first.timestamp())
        self.assertEqual(file_creation_date(info), first)
        self.assertIsNone(file_creation_date(SimpleNamespace(st_mtime=111, st_ctime=222)))

    def test_invalid_birth_time_has_no_fallback(self):
        self.assertIsNone(file_creation_date(SimpleNamespace(st_mtime=111, st_birthtime=float("nan"))))
