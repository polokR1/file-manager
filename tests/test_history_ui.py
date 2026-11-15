from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import tkinter as tk
import unittest
from unittest.mock import patch

from organizer_ui import OPERATIONS, OrganizerWindow


class HistoryWindowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = TemporaryDirectory()
        self.addCleanup(self.fixture.cleanup)
        self.folder = Path(self.fixture.name).resolve()
        self.source = self.folder / "Pobrane"
        self.source.mkdir()
        self.history_path = self.folder / "history.sqlite3"
        self.root, self.app = self.make_window()
        self.addCleanup(self.root.destroy)

    def make_window(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Brak środowiska graficznego Tk: {error}")
        root.withdraw()
        try:
            return root, OrganizerWindow(root, history_path=self.history_path)
        except Exception:
            root.destroy()
            raise

    def wait_for(self, root, app):
        deadline = monotonic() + 5
        while app.busy and monotonic() < deadline:
            root.update()
            sleep(0.01)
        self.assertFalse(app.busy)

    def test_move_can_be_undone_in_new_window_after_confirmation(self):
        original = self.source / "archive.pdf"
        original.write_bytes(b"important")
        self.app.source.set(str(self.source))
        self.app.operation.set(OPERATIONS[1])
        self.app.preview()
        self.wait_for(self.root, self.app)
        target = self.app.plan[0].destination
        with patch("organizer_ui.messagebox.askyesno", return_value=True):
            self.app.execute()
            self.wait_for(self.root, self.app)
        self.assertFalse(original.exists())
        self.assertEqual(target.read_bytes(), b"important")

        new_root, new_app = self.make_window()
        self.addCleanup(new_root.destroy)
        batch, = new_app.history.batches()
        with patch("organizer_ui.messagebox.askyesno", return_value=False):
            new_app.undo_history(batch.id)
        self.assertFalse(original.exists())
        with patch("organizer_ui.messagebox.askyesno", return_value=True):
            new_app.undo_history(batch.id)
            self.wait_for(new_root, new_app)
        self.assertEqual(original.read_bytes(), b"important")
        self.assertFalse(target.exists())
