import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import organizer


class FileAnalysisTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def write(self, name, data):
        file = self.root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        return file

    def test_reports_categories_and_largest_files_with_filtered_scan(self):
        from file_analysis import analyze_files

        self.write("report.PDF", b"123456")
        self.write("sub/IMG.JPG", b"12")
        self.write("notes.txt", b"1234")
        self.write("sorted/previous.JPG", b"12345")
        report = analyze_files(self.root, (".jpg", ".pdf"), True, exclude=self.root / "sorted")
        self.assertEqual(report.total_bytes, 8)
        self.assertEqual([f.path.name for f in report.files], ["report.PDF", "IMG.JPG"])
        self.assertEqual([(c.name, c.count, c.total_bytes) for c in report.categories],
                         [("Dokumenty", 1, 6), ("Zdjęcia", 1, 2)])

    def test_duplicates_require_matching_contents_regardless_of_name(self):
        from file_analysis import analyze_files

        first = self.write("a.jpg", b"abc")
        self.write("b.txt", b"abc")
        self.write("c.jpg", b"abd")
        self.write("sub/d.jpg", b"abc")
        report = analyze_files(self.root, ("*",), True)
        group, = report.duplicates
        self.assertCountEqual([f.path.name for f in group.files], ["a.jpg", "b.txt", "d.jpg"])
        self.assertEqual(group.redundant_bytes, 6)
        self.assertEqual(first.read_bytes(), b"abc")

    def test_hard_links_are_not_reported_as_independent_duplicates(self):
        from file_analysis import analyze_files

        first = self.write("a.jpg", b"abc")
        try:
            os.link(first, self.root / "z-link.jpg")
        except PermissionError:
            self.skipTest("Test environment does not permit hard links")
        self.assertEqual(analyze_files(self.root, ("*",), False).duplicates, ())

    def test_duplicate_scan_can_be_skipped(self):
        from file_analysis import analyze_files

        self.write("a.jpg", b"abc")
        self.write("b.jpg", b"abc")
        self.assertEqual(analyze_files(self.root, ("*",), False, find_duplicates=False).duplicates, ())

    def test_report_tracks_file_identity_for_later_duplicate_cleanup(self):
        from file_analysis import analyze_files

        original = self.write("photo.jpg", b"abc")
        first, = analyze_files(self.root, ("*",), False).files
        original.rename(self.root / "held-original.jpg")
        replacement = self.write("photo.jpg", b"abc")
        os.utime(replacement, (first.modified, first.modified))
        second = next(entry for entry in analyze_files(self.root, ("*",), False).files if entry.path == replacement)
        self.assertNotEqual(first.identity, second.identity)

    def test_unreadable_file_is_reported_without_stopping_duplicate_scan(self):
        from file_analysis import analyze_files

        denied = self.write("denied.jpg", b"abc")
        self.write("a.jpg", b"abc")
        self.write("b.jpg", b"abc")
        real_open = Path.open

        def open_file(path, *args, **kwargs):
            if path == denied:
                raise PermissionError("File is in use")
            return real_open(path, *args, **kwargs)

        with patch.object(Path, "open", open_file):
            report = analyze_files(self.root, ("*",), False)
        self.assertEqual([issue.path for issue in report.issues], [denied])
        self.assertEqual(report.total_bytes, 9)
        group, = report.duplicates
        self.assertEqual({file.path.name for file in group.files}, {"a.jpg", "b.jpg"})

    def test_cancellation_stops_analysis(self):
        from file_analysis import AnalysisCancelled, analyze_files

        self.write("a.jpg", b"abc")
        with self.assertRaises(AnalysisCancelled):
            analyze_files(self.root, ("*",), True, cancelled=lambda: True)

    def test_duplicate_scan_can_stop_after_reading_the_first_chunk(self):
        from file_analysis import AnalysisCancelled, fingerprint

        large = self.write("large.bin", b"a" * (2 * 1024 * 1024))

        def stop():
            raise AnalysisCancelled()

        with self.assertRaises(AnalysisCancelled):
            fingerprint(large, large.stat(), stop)


class OrganizingRulesTests(unittest.TestCase):
    def test_original_date_does_not_guess_from_filesystem_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory).resolve()
            file = source / "plain.txt"
            file.write_bytes(b"no embedded date")
            dest = source / "sorted"
            transfer, = organizer.build_plan(source, dest, ("*",), False, False, group_by_original_date=True)
            self.assertEqual(transfer.destination, dest / "Bez daty w metadanych" / file.name)

    @unittest.skipUnless(os.name == "nt", "Windows birth time")
    def test_creation_date_uses_windows_birth_time_instead_of_mtime(self):
        from datetime import datetime

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory).resolve()
            file = source / "plain.txt"
            file.touch()
            timestamp = datetime(1999, 4, 15, 12).timestamp()
            os.utime(file, (timestamp, timestamp))
            created = datetime.fromtimestamp(file.stat().st_birthtime)
            dest = source / "sorted"
            transfer, = organizer.build_plan(source, dest, ("*",), False, False, group_by_creation_date=True)
            self.assertEqual(transfer.destination, dest / f"{created.year:04d}" / f"{created.month:02d}" / file.name)
            self.assertNotEqual(transfer.destination, dest / "1999" / "04" / file.name)

    def test_date_group_uses_modification_month(self):
        from datetime import datetime

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory).resolve()
            dest = source / "sorted"
            file = source / "photo.jpg"
            file.touch()
            timestamp = datetime(2024, 2, 9, 12).timestamp()
            os.utime(file, (timestamp, timestamp))
            transfer, = organizer.build_plan(source, dest, ("*",), False, False, group_by_date=True)
            self.assertEqual(transfer.destination, dest / "2024" / "02" / "photo.jpg")
            self.assertFalse(dest.exists())

    def test_date_group_accepts_pre_1970_windows_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory).resolve()
            dest = source / "sorted"
            file = source / "archive.txt"
            file.touch()
            timestamp = -314582400  # 1960-01-15 UTC, away from a month boundary.
            os.utime(file, (timestamp, timestamp))
            transfer, = organizer.build_plan(source, dest, ("*",), False, False, group_by_date=True)
            self.assertEqual(transfer.destination, dest / "1960" / "01" / "archive.txt")

    def test_size_groups_use_binary_size_thresholds(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory).resolve()
            dest = source / "sorted"
            (source / "empty").touch()
            with (source / "medium").open("wb") as file:
                file.truncate(1024 * 1024)
            with (source / "large").open("wb") as file:
                file.truncate(100 * 1024 * 1024)
            result = organizer.build_plan(source, dest, ("*",), False, False, group_by_size=True)
            self.assertEqual({t.source.name: t.destination.parent.name for t in result},
                             {"empty": "Poniżej 1 MiB", "medium": "1–100 MiB", "large": "100 MiB i więcej"})

    def test_skip_existing_handles_existing_and_planned_names(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory).resolve()
            dest = source / "sorted"
            dest.mkdir()
            (dest / "report.PDF").write_bytes(b"keep")
            (source / "REPORT.pdf").touch()
            (source / "a.jpg").touch()
            (source / "nested").mkdir()
            (source / "nested" / "A.JPG").touch()
            result = organizer.build_plan(source, dest, ("*",), True, False, skip_existing=True)
            self.assertEqual([t.source for t in result], [source / "a.jpg"])
            self.assertEqual((dest / "report.PDF").read_bytes(), b"keep")
