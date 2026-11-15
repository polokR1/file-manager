import os
from pathlib import Path
from tempfile import TemporaryDirectory
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from duplicate_cleanup import recycle_duplicates
from file_analysis import FileInfo
from organizer_report_ui import CleanupDialog


def snapshot(path):
    info = path.stat()
    return FileInfo(path, info.st_size, "Dokumenty", info.st_mtime,
                    (info.st_dev, info.st_ino, info.st_mtime_ns, getattr(info, "st_birthtime_ns", info.st_ctime_ns)))


class DuplicateCleanupTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.source = self.base / "source"
        self.source.mkdir()
        self.bin = self.base / "test-bin"
        self.bin.mkdir()
        self.keeper = self.make_file(self.source / "keeper.txt")
        self.copy = self.make_file(self.source / "copy.txt")
        recycler = patch("duplicate_cleanup._recycle_file", side_effect=self.atomic_test_recycle)
        recycler.start()
        self.addCleanup(recycler.stop)

    @staticmethod
    def make_file(path, payload=b"original"):
        path.write_bytes(payload)
        return snapshot(path)

    def atomic_test_recycle(self, path, verify, descriptor):
        # Simulate the last preflight at the Windows boundary, then move to isolated storage.
        verify()
        if path.name == "blocked.txt":
            raise OSError("Kosz niedostępny")
        path.rename(self.bin / path.name)

    def test_only_selected_copies_move_and_keeper_is_unchanged(self):
        extra = self.make_file(self.source / "unselected.txt")
        result = recycle_duplicates(self.keeper, (self.copy,), source=self.source)
        self.assertEqual(result.recycled, (self.copy.path,))
        self.assertEqual(result.failures, ())
        self.assertEqual(self.keeper.path.read_bytes(), b"original")
        self.assertEqual(extra.path.read_bytes(), b"original")
        self.assertFalse(self.copy.path.exists())
        self.assertEqual((self.bin / self.copy.path.name).read_bytes(), b"original")

    def test_same_size_different_content_stays_in_place(self):
        other = self.make_file(self.source / "different.txt", b"DIFFERNT")
        result = recycle_duplicates(self.keeper, (other,), source=self.source)
        self.assertEqual(result.recycled, ())
        self.assertEqual(result.failures[0][0], other.path)
        self.assertEqual(other.path.read_bytes(), b"DIFFERNT")

    def test_modified_copy_is_rejected_even_when_contents_match(self):
        when = self.copy.path.stat().st_mtime_ns - 1_000_000_000
        os.utime(self.copy.path, ns=(when, when))
        result = recycle_duplicates(self.keeper, (self.copy,), source=self.source)
        self.assertEqual(result.recycled, ())
        self.assertEqual(result.failures[0][0], self.copy.path)
        self.assertTrue(self.copy.path.exists())

    def test_changed_keeper_prevents_cleanup(self):
        self.keeper.path.unlink()
        self.keeper.path.write_bytes(b"replacement")
        result = recycle_duplicates(self.keeper, (self.copy,), source=self.source)
        self.assertEqual(result.recycled, ())
        self.assertEqual(result.failures[0][0], self.copy.path)
        self.assertTrue(self.copy.path.exists())

    def test_explicitly_selected_keeper_and_outside_path_stay_in_place(self):
        outside = self.make_file(self.base / "outside.txt")
        result = recycle_duplicates(self.keeper, (self.keeper, outside), source=self.source)
        self.assertEqual(result.recycled, ())
        self.assertEqual({path for path, _ in result.failures}, {self.keeper.path, outside.path})
        self.assertTrue(self.keeper.path.exists())
        self.assertTrue(outside.path.exists())

    def test_hard_link_to_keeper_stays_in_place(self):
        linked = self.source / "linked.txt"
        try:
            os.link(self.keeper.path, linked)
        except PermissionError as error:
            self.skipTest(f"Nie można utworzyć dowiązania twardego: {error}")
        # Refresh the keeper snapshot because link creation updates metadata.
        result = recycle_duplicates(snapshot(self.keeper.path), (snapshot(linked),), source=self.source)
        self.assertEqual(result.recycled, ())
        self.assertEqual(result.failures[0][0], linked)
        self.assertEqual(linked.read_bytes(), b"original")

    def test_recycle_failure_preserves_copy_and_reports_partial_success(self):
        blocked = self.make_file(self.source / "blocked.txt")
        result = recycle_duplicates(self.keeper, (blocked, self.copy), source=self.source)
        self.assertEqual(result.recycled, (self.copy.path,), result.failures)
        self.assertEqual(result.failures[0][0], blocked.path)
        self.assertEqual(blocked.path.read_bytes(), b"original")

    def test_path_substitution_at_native_boundary_is_rejected(self):
        def substitute(path, verify, descriptor):
            path.rename(path.with_suffix(".bak"))
            path.write_bytes(b"original")
            self.atomic_test_recycle(path, verify, descriptor)

        with patch("duplicate_cleanup._recycle_file", side_effect=substitute):
            result = recycle_duplicates(self.keeper, (self.copy,), source=self.source)
        self.assertEqual(result.recycled, ())
        self.assertEqual(result.failures[0][0], self.copy.path)
        self.assertTrue(self.copy.path.exists())


class CleanupDialogTests(unittest.TestCase):
    def test_requires_an_explicit_keeper_and_checked_copy(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Brak środowiska graficznego Tk: {error}")
        self.addCleanup(root.destroy)
        root.withdraw()
        source = Path.cwd()
        files = tuple(FileInfo(source / f"{n}.txt", 4, "Dokumenty", 0) for n in (1, 2, 3))
        applied = []
        dialog = CleanupDialog(root, source, files, lambda keeper, copies: applied.append((keeper, copies)))
        controls = {
            child.cget("text"): child
            for frame in dialog.winfo_children()
            for child in frame.winfo_children()
            if isinstance(child, ttk.Button)
        }
        keep = controls["Zachowaj"]
        mark = controls["Zaznacz do Kosza"]
        apply = controls["Przenieś do Kosza"]
        rows = dialog.table.get_children()

        self.assertTrue(apply.instate(["disabled"]))
        dialog.table.selection_set(rows[0])
        root.update()
        keep.invoke()
        self.assertTrue(mark.instate(["disabled"]))
        self.assertTrue(apply.instate(["disabled"]))
        dialog.table.selection_set(rows[1])
        root.update()
        mark.invoke()
        apply.invoke()
        self.assertEqual(applied, [(files[0], (files[1],))])


@unittest.skipUnless(os.name == "nt", "Wymagany Windows")
class NativeRecycleValidationTests(unittest.TestCase):
    def test_copy_with_extra_data_stream_is_rejected_before_recycle(self):
        with TemporaryDirectory() as temporary:
            source = Path(temporary).resolve()
            keeper = source / "keeper.txt"
            copy = source / "copy.txt"
            keeper.write_bytes(b"original")
            copy.write_bytes(b"original")
            alternate = Path(f"{copy}:extra-data")
            try:
                alternate.write_bytes(b"additional data")
            except OSError as error:
                self.skipTest(f"Nie można utworzyć dodatkowego strumienia danych: {error}")

            with patch("duplicate_cleanup._shell_recycle") as transport:
                result = recycle_duplicates(snapshot(keeper), (snapshot(copy),), source=source)

            self.assertFalse(result.recycled)
            self.assertEqual(result.failures[0][0], copy)
            self.assertEqual(copy.read_bytes(), b"original")
            self.assertEqual(alternate.read_bytes(), b"additional data")
            transport.assert_not_called()
