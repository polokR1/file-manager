import errno
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import organizer
from organizer import Transfer, build_plan


class OperationFeedbackTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.source = root / "source"
        self.source.mkdir()
        self.dest = root / "destination"

    def test_reports_inaccessible_file_and_still_plans_accessible_files(self):
        good = self.source / "a.txt"
        blocked = self.source / "b.txt"
        good.touch()
        blocked.touch()
        real_stat = Path.lstat

        def stat_file(path):
            if path == blocked:
                raise PermissionError(errno.EACCES, "access denied", str(path))
            return real_stat(path)

        issues = []
        with patch.object(Path, "lstat", stat_file):
            plan = build_plan(self.source, self.dest, ("*",), False, False, issues=issues)
        self.assertEqual([item.source for item in plan], [good])
        self.assertEqual([issue.path for issue in issues], [blocked])

    def test_profile_skips_recent_files_and_named_folders_at_any_depth(self):
        old = self.source / "old.txt"
        old.touch()
        recent = self.source / "recent.txt"
        recent.touch()
        excluded = self.source / "sub" / "Ignore" / "notes.txt"
        excluded.parent.mkdir(parents=True)
        excluded.touch()
        older_time = time.time() - 14 * 86_400
        for path in (old, excluded):
            os.utime(path, (older_time, older_time))
        plan = build_plan(self.source, self.dest, ("*",), True, False,
                          min_age_days=7, excluded_folders=("ignore",))
        self.assertEqual([item.source for item in plan], [old])

    def test_preflight_flags_insufficient_space_without_creating_target(self):
        from operation_feedback import check_destination

        original = self.source / "large.txt"
        original.write_bytes(b"01234")
        transfer = Transfer(original, self.dest / "large.txt")
        with patch("operation_feedback.shutil.disk_usage", return_value=shutil._ntuple_diskusage(10, 7, 3)):
            with self.assertRaises(OSError) as caught:
                check_destination(self.dest, (transfer,), moving=False)
        self.assertEqual(caught.exception.errno, errno.ENOSPC)
        self.assertFalse(self.dest.exists())

    def test_preflight_and_reports_are_readable_and_leave_no_probe(self):
        from operation_feedback import check_destination, describe_issue

        original = self.source / "note.txt"
        original.write_bytes(b"abc")
        transfer = Transfer(original, self.dest / "note.txt")
        check_destination(self.dest, (transfer,), moving=False)
        self.assertEqual(list(self.source.parent.iterdir()), [self.source])
        self.assertIn("Brak dostępu", describe_issue(PermissionError(errno.EACCES, "raw"), "pl"))
        self.assertIn("Access denied", describe_issue(PermissionError(errno.EACCES, "raw"), "en"))
