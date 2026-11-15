import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from duplicate_cleanup import CleanupResult
from file_analysis import FileInfo, analyze_files
from organizer_history_ui import HistoryPanel
from organizer_report_ui import CleanupDialog, ReportPanel
from transfer_history import HistoryBatch, UndoIssue, UndoResult


class PanelLocalizationTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk is unavailable: {error}")
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.source = Path(folder.name).resolve()

    def test_english_report_translates_categories_and_duplicate_summary(self):
        (self.source / "first.pdf").write_bytes(b"x" * 1280)
        (self.source / "second.pdf").write_bytes(b"x" * 1280)
        report = analyze_files(self.source, ("*",), False)
        panel = ReportPanel(self.root, lambda *_: None, lambda: None, language="en")

        panel.show(self.source, report, True)

        self.assertEqual(panel.analyze_button.cget("text"), "Analyze folder")
        self.assertEqual(panel.views.tab(panel.duplicates.master, "text"), "Duplicates")
        self.assertIn("Duplicate groups (1)", panel.hint.get())
        category, = panel.categories.get_children()
        fields = panel.categories.item(category, "values")
        self.assertEqual((fields[0], fields[2]), ("Documents", "2.5 KiB"))

    def test_polish_report_remains_default(self):
        panel = ReportPanel(self.root, lambda *_: None, lambda: None)

        self.assertEqual(panel.analyze_button.cget("text"), "Analizuj folder")
        self.assertEqual(panel.views.tab(panel.duplicates.master, "text"), "Duplikaty")

    def test_english_cleanup_keeps_original_diagnostics(self):
        panel = ReportPanel(self.root, lambda *_: None, lambda: None, language="en")
        original = "Plik zmienił się od czasu analizy."
        result = CleanupResult((), ((self.source / "f.txt", original),))

        panel.show_cleanup(result)

        self.assertIn("Failed (1)", panel.summary.get())
        issue, = panel.issues.get_children()
        displayed = panel.issues.set(issue, "message")
        self.assertIn("Details", displayed)
        self.assertIn(original, displayed)

    def test_english_cleanup_dialog_uses_localized_labels_during_selection(self):
        files = tuple(FileInfo(self.source / f"{index}.txt", 5, "Dokumenty", 0) for index in (1, 2))
        dialog = CleanupDialog(self.root, self.source, files, lambda *_: None, language="en")
        self.addCleanup(dialog.destroy)

        self.assertEqual(dialog.title(), "Clean up duplicate group")
        first, second = dialog.table.get_children()
        dialog.table.selection_set(first)
        self.root.update()
        dialog.keep_button.invoke()
        dialog.table.selection_set(second)
        self.root.update()
        dialog.check_button.invoke()

        self.assertEqual(dialog.table.set(first, "decision"), "Keep")
        self.assertEqual(dialog.table.set(second, "decision"), "Recycle")
        self.assertIn("(1)", dialog.status.get())

    def test_english_history_localizes_operation_issues_and_actions(self):
        panel = HistoryPanel(self.root, lambda *_: None, language="en")
        panel.show_batches([HistoryBatch(1, "2026-11-15 12:00:00", True, 1, 0, 1)])
        panel.table.selection_set("1")
        self.root.update()

        self.assertEqual(panel.table.set("1", "operation"), "Move")
        self.assertEqual(panel.undo_button.cget("text"), "Review interrupted operation")
        original = "Ścieżka jest już zajęta."
        panel.show_result(UndoResult(0, (UndoIssue(self.source / "f.txt", original),)))
        issue, = panel.issues.get_children()
        self.assertIn("Files restored", panel.summary.get())
        displayed = panel.issues.set(issue, "message")
        self.assertIn("Details", displayed)
        self.assertIn(original, displayed)

    def test_polish_history_remains_default(self):
        panel = HistoryPanel(self.root, lambda *_: None)
        panel.show_batches([HistoryBatch(1, "2026-11-15 12:00:00", False, 1, 0, 0)])

        self.assertEqual(panel.table.set("1", "operation"), "Kopiowanie")
