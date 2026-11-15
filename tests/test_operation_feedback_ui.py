import errno
import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest.mock import patch

from organizer_ui import OrganizerWindow


class RetryWindowTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.work = Path(folder.name).resolve()
        self.source = self.work / "source"
        self.source.mkdir()
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(root.destroy)
        root.withdraw()
        self.root = root
        self.app = OrganizerWindow(root, history_path=self.work / "history.sqlite3")
        self.app.source.set(str(self.source))

    def wait_for_worker(self):
        deadline = monotonic() + 10
        while self.app.busy and monotonic() < deadline:
            self.root.update()
            sleep(0.01)
        self.assertFalse(self.app.busy)

    def test_retry_only_reprocesses_failed_items(self):
        (self.source / "failed.txt").write_bytes(b"failed input")
        (self.source / "success.txt").write_bytes(b"original successful output")
        app = self.app
        app.preview()
        self.wait_for_worker()
        failed = next(item.destination for item in app.plan if item.source.name == "failed.txt")
        success = next(item.destination for item in app.plan if item.source.name == "success.txt")
        failed.parent.mkdir(parents=True)
        failed.write_bytes(b"conflicting destination")
        app.execute()
        self.wait_for_worker()
        self.assertEqual(success.read_bytes(), b"original successful output")
        self.assertEqual(failed.read_bytes(), b"conflicting destination")

        failed.unlink()
        (self.source / "success.txt").write_bytes(b"do not use this")
        app.retry_failed()
        self.wait_for_worker()
        self.assertEqual(failed.read_bytes(), b"failed input")
        self.assertEqual(success.read_bytes(), b"original successful output")
        self.assertEqual({path.name for path in success.parent.iterdir()}, {"failed.txt", "success.txt"})

    def test_preflight_failure_preserves_preview_for_another_attempt(self):
        (self.source / "one.txt").write_bytes(b"file")
        self.app.preview()
        self.wait_for_worker()
        transfer, = self.app.plan
        with patch("organizer_ui.check_destination", side_effect=OSError(errno.ENOSPC, "disk full")):
            self.app.execute()
            self.wait_for_worker()
        self.assertFalse(transfer.destination.exists())
        self.assertIn("miejsca", self.app.status.get())
        self.assertEqual(self.app.plan, [transfer])

        self.app.execute()
        self.wait_for_worker()
        self.assertEqual(transfer.destination.read_bytes(), b"file")

    def test_preview_still_lists_files_when_one_cannot_be_accessed(self):
        good = self.source / "good.txt"
        blocked = self.source / "blocked.txt"
        for path in (good, blocked):
            path.touch()
        real_lstat = Path.lstat

        def lstat(path):
            if path == blocked:
                raise PermissionError(errno.EACCES, "simulated access denial", str(path))
            return real_lstat(path)

        with patch.object(Path, "lstat", lstat):
            self.app.preview()
            self.wait_for_worker()
        self.assertEqual([item.source for item in self.app.plan], [good])
        self.assertFalse(self.app.preview_issues_button.instate(["disabled"]))
