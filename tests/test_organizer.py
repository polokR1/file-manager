import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from organizer import build_plan, parse_extensions, transfer_file


class ParseExtensionsTests(unittest.TestCase):
    def test_normalizes_dots_case_and_separators(self):
        extensions = parse_extensions(" JPG, .PDF; png\tTXT\n")

        self.assertCountEqual(extensions, (".jpg", ".pdf", ".png", ".txt"))

    def test_all_files_filter_includes_files_without_extensions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source"
            destination = Path(temp_dir) / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "README").touch()
            (source / "image.jpg").touch()

            plan = build_plan(source, destination, parse_extensions(" * "), False, False)

        self.assertCountEqual((item.source.name for item in plan), ("README", "image.jpg"))

    def test_rejects_empty_or_invalid_filters(self):
        for value in ("", ", ;  ", "* jpg", "foo/bar"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_extensions(value)


class OrganizerTests(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name).resolve()
        self.source = root / "source"
        self.destination = root / "destination"
        self.source.mkdir()
        self.destination.mkdir()

    def plan(self, extension="jpg", recursive=True, group_by_type=False):
        return build_plan(
            self.source,
            self.destination,
            parse_extensions(extension),
            recursive,
            group_by_type,
        )

    def test_recursion_and_case_insensitive_matching(self):
        nested = self.source / "subfolder"
        nested.mkdir()
        root_photo = self.source / "root.JPG"
        nested_photo = nested / "nested.Jpg"
        for file in (root_photo, nested_photo, nested / "notes.txt"):
            file.touch()

        self.assertEqual([item.source for item in self.plan(recursive=False)], [root_photo])
        self.assertCountEqual((item.source for item in self.plan()), (root_photo, nested_photo))

    def test_plan_reserves_names_with_existing_and_planned_case_conflicts(self):
        nested = self.source / "subfolder"
        nested.mkdir()
        (self.source / "Photo.JPG").touch()
        (nested / "photo.jpg").touch()
        existing = self.destination / "PHOTO.jpg"
        existing.write_bytes(b"original")

        plan = self.plan()

        self.assertCountEqual(
            (item.destination.name.casefold() for item in plan),
            ("photo_1.jpg", "photo_2.jpg"),
        )
        self.assertEqual(list(self.destination.iterdir()), [existing])
        self.assertEqual(existing.read_bytes(), b"original")
        self.assertTrue(all(item.source.exists() for item in plan))

    def test_grouping_uses_extension_or_no_extension_folder(self):
        (self.source / "image.jpg").touch()
        (self.source / "README").touch()

        plan = self.plan(extension="*", group_by_type=True)

        self.assertCountEqual(
            (item.destination.relative_to(self.destination) for item in plan),
            (Path("JPG/image.jpg"), Path("Bez rozszerzenia/README")),
        )
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_groups_known_file_types_by_category_and_unknown_types_as_other(self):
        for name in ("photo.JPG", "scan.png", "invoice.pdf", "README"):
            (self.source / name).touch()

        plan = build_plan(
            self.source,
            self.destination,
            parse_extensions("*"),
            False,
            False,
            group_by_category=True,
        )

        self.assertCountEqual(
            (item.destination.relative_to(self.destination) for item in plan),
            (
                Path("Zdjęcia/photo.JPG"),
                Path("Zdjęcia/scan.png"),
                Path("Dokumenty/invoice.pdf"),
                Path("Inne/README"),
            ),
        )

    def test_rejects_same_folder(self):
        with self.assertRaises(ValueError):
            build_plan(self.source, self.source, parse_extensions("jpg"), False, False)

    def test_recursive_search_skips_nested_destination(self):
        nested_destination = self.source / "output"
        nested_destination.mkdir()
        already_sorted = nested_destination / "image.jpg"
        already_sorted.write_bytes(b"existing")
        unsorted_folder = self.source / "unsorted"
        unsorted_folder.mkdir()
        unsorted = unsorted_folder / "image.jpg"
        unsorted.write_bytes(b"incoming")

        transfer, = build_plan(
            self.source, nested_destination, parse_extensions("jpg"), True, False
        )

        self.assertEqual(transfer.source, unsorted)
        self.assertEqual(transfer.destination, nested_destination / "image_1.jpg")
        self.assertEqual(already_sorted.read_bytes(), b"existing")

    def test_plan_allows_new_destination_without_creating_it(self):
        new_destination = self.source / "output"
        (self.source / "image.jpg").touch()

        transfer, = build_plan(
            self.source, new_destination, parse_extensions("jpg"), True, False
        )

        self.assertEqual(transfer.destination, new_destination / "image.jpg")
        self.assertFalse(new_destination.exists())

    def test_copy_preserves_origin_and_creates_group_folder(self):
        original = self.source / "image.jpg"
        original.write_bytes(b"image data\x00\xff")
        transfer, = self.plan(group_by_type=True)

        transfer_file(transfer, move=False)

        self.assertEqual(original.read_bytes(), b"image data\x00\xff")
        self.assertEqual(transfer.destination.read_bytes(), original.read_bytes())

    def test_move_removes_origin_and_preserves_data(self):
        original = self.source / "image.jpg"
        original.write_bytes(b"image data\x00\xff")
        transfer, = self.plan()

        transfer_file(transfer, move=True)

        self.assertFalse(original.exists())
        self.assertEqual(transfer.destination.read_bytes(), b"image data\x00\xff")

    def test_move_refuses_destination_created_after_preview(self):
        original = self.source / "image.jpg"
        original.write_bytes(b"incoming")
        transfer, = self.plan()
        transfer.destination.write_bytes(b"already there")

        with self.assertRaises(OSError):
            transfer_file(transfer, move=True)

        self.assertEqual(original.read_bytes(), b"incoming")
        self.assertEqual(transfer.destination.read_bytes(), b"already there")

    def test_copy_refuses_destination_created_after_preview(self):
        original = self.source / "image.jpg"
        original.write_bytes(b"incoming")
        transfer, = self.plan()
        transfer.destination.write_bytes(b"already there")

        with self.assertRaises(FileExistsError):
            transfer_file(transfer, move=False)

        self.assertEqual(original.read_bytes(), b"incoming")
        self.assertEqual(transfer.destination.read_bytes(), b"already there")

    def test_move_rolls_back_copy_if_origin_cannot_be_removed(self):
        original = self.source / "image.jpg"
        original.write_bytes(b"incoming")
        transfer, = self.plan()
        real_unlink = Path.unlink
        real_rename = os.rename

        def deny_removing_origin(path, *args, **kwargs):
            if path == original:
                raise PermissionError("source is locked")
            return real_unlink(path, *args, **kwargs)

        def simulate_cross_volume_rename(path, target):
            if path == original:
                raise OSError(errno.EXDEV, "different disk")
            return real_rename(path, target)

        with (
            patch("organizer.os.rename", side_effect=simulate_cross_volume_rename),
            patch.object(Path, "unlink", deny_removing_origin),
        ):
            with self.assertRaises(OSError):
                transfer_file(transfer, move=True)

        self.assertEqual(original.read_bytes(), b"incoming")
        self.assertFalse(transfer.destination.exists())


if __name__ == "__main__":
    unittest.main()
