import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import tkinter as tk
from tkinter import ttk
import unittest

from organizer_ui import OrganizerWindow


class OrganizerWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Brak środowiska graficznego Tk: {error}")
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        self.app = OrganizerWindow(self.root)
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.source = Path(scratch.name).resolve() / "Pobrane"
        self.source.mkdir()
        self.destination = self.source / "Uporządkowane"
        self.app.source.set(str(self.source))

    def wait_for_worker(self):
        deadline = monotonic() + 5
        while self.app.busy and monotonic() < deadline:
            self.root.update()
            sleep(0.01)
        self.assertFalse(self.app.busy, "Operacja nie zakończyła się w ciągu 5 sekund.")

    def find_button(self, label, parent=None):
        for widget in (self.root if parent is None else parent).winfo_children():
            if isinstance(widget, ttk.Button) and widget.cget("text") == label:
                return widget
            match = self.find_button(label, widget)
            if match is not None:
                return match

    def start_report(self, label):
        button = self.find_button(label)
        self.assertIsNotNone(button, f"Brak przycisku {label}")
        self.app.tabs.select(self.app.report)
        button.invoke()
        self.wait_for_worker()
        return self.app.report

    def test_analysis_respects_scope_and_excludes_output_folder(self):
        (self.source / "photo.jpg").write_bytes(b"abc")
        (self.source / "notes.txt").write_bytes(b"text")
        self.destination.mkdir()
        (self.destination / "old.jpg").write_bytes(b"excluded")
        self.app.recursive.set(True)
        self.app.extensions.set("jpg")

        report = self.start_report("Analizuj folder")

        rows = report.largest.get_children()
        self.assertEqual(len(rows), 1)
        self.assertEqual(report.largest.set(rows[0], "path"), "photo.jpg")
        self.assertEqual(report.largest.set(rows[0], "size"), "3 B")
        self.assertTrue((self.source / "photo.jpg").is_file())

    def test_duplicate_button_lists_copies_without_removing_them(self):
        (self.source / "one.jpg").write_bytes(b"abc")
        (self.source / "copy.txt").write_bytes(b"abc")
        (self.source / "different.txt").write_bytes(b"xyz")

        report = self.start_report("Szukaj duplikatów")

        groups = report.duplicates.get_children()
        self.assertEqual(len(groups), 1)
        copies = report.duplicates.get_children(groups[0])
        self.assertEqual({report.duplicates.item(row, "text") for row in copies}, {"one.jpg", "copy.txt"})
        self.assertEqual(report.duplicates.set(groups[0], "size"), "3 B")
        self.assertTrue((self.source / "one.jpg").is_file())
        self.assertTrue((self.source / "copy.txt").is_file())

    def test_changed_source_scope_invalidates_the_current_report(self):
        (self.source / "photo.jpg").write_bytes(b"abc")
        report = self.start_report("Analizuj folder")
        self.assertTrue(report.largest.get_children())

        self.app.extensions.set("txt")

        self.assertFalse(report.largest.get_children())

    def test_analysis_displays_pre_1970_file_dates(self):
        old_file = self.source / "history.txt"
        old_file.write_bytes(b"archive")
        before_1970 = -126_187_200
        os.utime(old_file, (before_1970, before_1970))

        report = self.start_report("Analizuj folder")

        rows = report.largest.get_children()
        self.assertEqual(len(rows), 1)
        self.assertTrue(report.largest.set(rows[0], "modified").endswith("1966"))

    def test_sorting_can_group_by_size_and_skip_name_conflicts(self):
        (self.source / "kept.txt").write_bytes(b"data")
        (self.source / "moved.txt").write_bytes(b"more")
        size_option = "Rozmiar"
        grouping_box = getattr(self.app, "grouping_box", None)
        self.assertIsNotNone(grouping_box, "Brak listy opcji grupowania.")
        self.assertIn(size_option, grouping_box.cget("values"))
        self.app.grouping.set(size_option)
        self.app.preview()
        self.wait_for_worker()
        self.assertEqual(len(self.app.plan), 2)
        destination_group = self.app.plan[0].destination.parent
        self.assertNotEqual(destination_group, self.destination)
        destination_group.mkdir(parents=True)
        (destination_group / "kept.txt").write_bytes(b"existing")
        self.app.conflict.set("Pomiń plik")

        self.app.preview()
        self.wait_for_worker()

        self.assertEqual([row.source.name for row in self.app.plan], ["moved.txt"])


if __name__ == "__main__":
    unittest.main()
