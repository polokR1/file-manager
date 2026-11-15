import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import transfer_history
from organizer import Transfer, transfer_file
from transfer_history import TransferHistory


class TransferHistoryTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.db = self.root / "settings" / "history.sqlite3"
        self.history = TransferHistory(self.db)
        self.item = Transfer(self.root / "source" / "foto.jpg", self.root / "sorted" / "foto.jpg")
        self.item.source.parent.mkdir()
        self.item.source.write_bytes(b"foto-01")

    def restart(self):
        return TransferHistory(self.db)

    def move_file(self):
        batch = self.history.start_batch(move=True)
        self.history.transfer(batch, self.item, move=True)
        return batch

    def test_opening_empty_history_does_not_create_config_directory(self):
        self.assertEqual(self.history.batches(), [])
        self.assertFalse(self.db.parent.exists())

    def test_history_survives_restart_and_moves_can_be_undone_once(self):
        batch = self.move_file()
        restarted = self.restart()
        record, = restarted.batches()

        self.assertEqual((record.id, record.move, record.completed, record.remaining), (batch, True, 1, 1))
        self.assertTrue(record.created_at)
        result = restarted.undo(batch)
        self.assertEqual((result.restored, result.issues), (1, ()))
        self.assertEqual(self.item.source.read_bytes(), b"foto-01")
        self.assertFalse(self.item.destination.exists())
        self.assertEqual(self.restart().batches()[0].remaining, 0)
        self.assertEqual(self.restart().undo(batch).restored, 0)

    def test_copy_is_recorded_without_becoming_undoable(self):
        batch = self.history.start_batch(move=False)
        self.history.transfer(batch, self.item, move=False)

        record, = self.restart().batches()
        self.assertEqual((record.id, record.move, record.completed, record.remaining), (batch, False, 1, 0))
        self.assertEqual(self.restart().undo(batch).restored, 0)
        self.assertEqual(self.item.destination.read_bytes(), self.item.source.read_bytes())

    def test_journal_is_kept_in_place_if_its_directory_is_selected(self):
        batch = self.history.start_batch(move=True)
        item = Transfer(self.db, self.root / "sorted" / "history.sqlite3")

        with self.assertRaises(ValueError):
            self.history.transfer(batch, item, move=True)

        self.assertTrue(self.db.is_file())
        self.assertFalse(item.destination.exists())
        record = self.restart().batches()[0]
        self.assertEqual((record.completed, record.uncertain), (0, 0))

    def test_failed_transfers_do_not_count_as_completed(self):
        self.item.destination.parent.mkdir()
        self.item.destination.write_bytes(b"already here")
        batch = self.history.start_batch(move=True)

        with self.assertRaises(FileExistsError):
            self.history.transfer(batch, self.item, move=True)

        result = self.restart().undo(batch)
        self.assertEqual((result.restored, result.issues), (0, ()))
        self.assertEqual(self.restart().batches()[0].completed, 0)
        self.assertEqual(self.item.source.read_bytes(), b"foto-01")
        self.assertEqual(self.item.destination.read_bytes(), b"already here")

    def test_source_changed_before_transfer_is_a_known_failure(self):
        batch = self.history.start_batch(move=True)
        read_stat = transfer_history._file_stat
        source_reads = 0

        def edit_while_starting(path):
            nonlocal source_reads
            if path == self.item.source:
                source_reads += 1
                if source_reads == 2:
                    path.write_bytes(b"edited before moving")
            return read_stat(path)

        with patch("transfer_history._file_stat", side_effect=edit_while_starting):
            with self.assertRaises(ValueError):
                self.history.transfer(batch, self.item, move=True)

        self.assertEqual(self.item.source.read_bytes(), b"edited before moving")
        self.assertFalse(self.item.destination.exists())
        record, = self.restart().batches()
        self.assertEqual((record.completed, record.remaining, record.uncertain), (0, 0, 0))

    def test_undo_never_overwrites_original_path_and_can_retry(self):
        batch = self.move_file()
        self.item.source.write_bytes(b"different data")

        result = self.restart().undo(batch)

        self.assertEqual(result.restored, 0)
        self.assertEqual(result.issues[0].path, self.item.source)
        self.assertEqual(self.item.source.read_bytes(), b"different data")
        self.assertEqual(self.item.destination.read_bytes(), b"foto-01")
        self.assertEqual(self.restart().batches()[0].remaining, 1)
        self.item.source.unlink()
        self.assertEqual(self.restart().undo(batch).restored, 1)

    def test_undo_refuses_changed_destination(self):
        batch = self.move_file()
        self.item.destination.write_bytes(b"changed content")

        self.assertEqual(self.restart().undo(batch).restored, 0)
        self.assertFalse(self.item.source.exists())
        self.assertEqual(self.item.destination.read_bytes(), b"changed content")

    def test_undo_refuses_replacement_with_identical_contents_and_mtime(self):
        batch = self.move_file()
        recorded = self.item.destination.stat()
        self.item.destination.rename(self.item.destination.with_name("old.jpg"))
        self.item.destination.write_bytes(b"foto-01")
        os.utime(self.item.destination, ns=(recorded.st_atime_ns, recorded.st_mtime_ns))

        result = self.restart().undo(batch)

        self.assertEqual(result.restored, 0)
        self.assertTrue(result.issues)
        self.assertFalse(self.item.source.exists())
        self.assertEqual(self.item.destination.read_bytes(), b"foto-01")

    def test_undo_refuses_recreated_original_directory(self):
        batch = self.move_file()
        self.item.source.parent.rename(self.root / "old_source")
        self.item.source.parent.mkdir()

        result = self.restart().undo(batch)

        self.assertEqual(result.restored, 0)
        self.assertTrue(result.issues)
        self.assertFalse(self.item.source.exists())
        self.assertEqual(self.item.destination.read_bytes(), b"foto-01")

    def test_undo_refuses_recreated_destination_directory_with_same_file(self):
        batch = self.move_file()
        old = self.item.destination.parent.rename(self.root / "old_sorted")
        self.item.destination.parent.mkdir()
        (old / self.item.destination.name).rename(self.item.destination)

        self.assertEqual(self.restart().undo(batch).restored, 0)
        self.assertEqual(self.item.destination.read_bytes(), b"foto-01")

    def test_undo_preserves_dangling_link_at_original_path(self):
        batch = self.move_file()
        missing = self.root / "missing.jpg"
        try:
            self.item.source.symlink_to(missing)
        except OSError as error:
            self.skipTest(f"Test environment does not permit symlinks: {error}")

        self.assertEqual(self.restart().undo(batch).restored, 0)
        self.assertTrue(self.item.source.is_symlink())
        self.assertEqual(self.item.destination.read_bytes(), b"foto-01")

    def test_pending_forward_move_is_not_reversed_based_on_path_guesses(self):
        batch = self.history.start_batch(move=True)

        def move_then_interrupt(item, move):
            transfer_file(item, move=move)
            raise KeyboardInterrupt()

        with patch("transfer_history.transfer_file", side_effect=move_then_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.history.transfer(batch, self.item, move=True)

        persisted = self.restart()
        record, = persisted.batches()
        self.assertEqual((record.completed, record.remaining, record.uncertain), (0, 0, 1))
        result = persisted.undo(batch)
        self.assertEqual(result.restored, 0)
        self.assertTrue(result.issues)
        self.assertEqual(self.item.destination.read_bytes(), b"foto-01")

    def test_move_that_errors_after_relocation_is_not_safe_to_retry(self):
        batch = self.history.start_batch(move=True)

        def move_then_fail(item, move):
            transfer_file(item, move=move)
            raise OSError("staging cleanup failed")

        with patch("transfer_history.transfer_file", side_effect=move_then_fail):
            with self.assertRaises(transfer_history.HistoryError):
                self.history.transfer(batch, self.item, move=True)

        journal_batch, = self.restart().batches()
        self.assertEqual(journal_batch.uncertain, 1)
        self.assertEqual(self.item.destination.read_bytes(), b"foto-01")

    def test_undo_interrupted_after_relocation_does_not_retry_the_file(self):
        batch = self.move_file()

        def move_then_interrupt(item, move):
            transfer_file(item, move=move)
            raise KeyboardInterrupt()

        with patch("transfer_history.transfer_file", side_effect=move_then_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.history.undo(batch)

        persisted = self.restart()
        record, = persisted.batches()
        self.assertEqual((record.completed, record.remaining, record.uncertain), (1, 0, 1))
        result = persisted.undo(batch)
        self.assertEqual(result.restored, 0)
        self.assertTrue(result.issues)
        self.assertEqual(self.item.source.read_bytes(), b"foto-01")
        self.assertFalse(self.item.destination.exists())

    def test_blocked_item_does_not_prevent_other_files_from_being_undone(self):
        batch = self.move_file()
        second = Transfer(self.item.source.with_name("second.jpg"), self.item.destination.with_name("second.jpg"))
        second.source.write_bytes(b"second file")
        self.history.transfer(batch, second, move=True)
        second.source.write_bytes(b"name was reused")

        result = self.restart().undo(batch)

        self.assertEqual(result.restored, 1)
        self.assertEqual([issue.path for issue in result.issues], [second.source])
        self.assertEqual(self.item.source.read_bytes(), b"foto-01")
        self.assertEqual(second.source.read_bytes(), b"name was reused")
        self.assertEqual(second.destination.read_bytes(), b"second file")


if __name__ == "__main__":
    unittest.main()
