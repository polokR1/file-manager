from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import tkinter as tk
import unittest
from unittest.mock import patch

from file_analysis import analyze_files
from organizer_ui import OrganizerWindow


class RecycleWindowTests(unittest.TestCase):
    def test_cancel_preserves_copy_and_confirm_only_submits_requested_file(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Brak środowiska graficznego Tk: {error}")
        self.addCleanup(root.destroy)
        root.withdraw()
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name).resolve()
        for name in ("a.txt", "b.txt", "c.txt"):
            (directory / name).write_bytes(b"same")
        app = OrganizerWindow(root, history_path=directory / "history.sqlite3")
        app.source.set(str(directory))
        group, = analyze_files(directory, ("*",), False).duplicates
        keeper, selected, other = group.files
        from duplicate_cleanup import CleanupResult

        # Keep the platform Recycle Bin outside this UI test. A separate native test checks it.
        def emulate_shell_action(keep, copies, *, source):
            self.assertEqual(keep, keeper)
            self.assertEqual(source, directory)
            for copy in copies:
                copy.path.rename(directory / "recycled.tmp")
            return CleanupResult(tuple(copy.path for copy in copies), ())

        with patch("organizer_ui.recycle_duplicate_files", side_effect=emulate_shell_action):
            with patch("organizer_ui.messagebox.askyesno", return_value=False):
                app.recycle_duplicates(keeper, (selected,))
            self.assertTrue(selected.path.exists())
            with patch("organizer_ui.messagebox.askyesno", return_value=True):
                app.recycle_duplicates(keeper, (selected,))
                deadline = monotonic() + 5
                while app.busy and monotonic() < deadline:
                    root.update()
                    sleep(0.01)
            self.assertFalse(app.busy)
        self.assertFalse(selected.path.exists())
        self.assertEqual((directory / "recycled.tmp").read_bytes(), b"same")
        self.assertTrue(keeper.path.exists())
        self.assertTrue(other.path.exists())
