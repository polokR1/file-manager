import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep, time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from organizer_ui import OrganizerWindow


class MainPreferencesTests(unittest.TestCase):
    def setUp(self):
        fixture = TemporaryDirectory()
        self.addCleanup(fixture.cleanup)
        self.folder = Path(fixture.name).resolve()
        self.preferences = self.folder / "preferences.json"
        self.history = self.folder / "history.sqlite3"
        self.app = self.window()

    def window(self, use_preferences_path=False):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(root.destroy)
        root.withdraw()
        options = {"preferences_path": self.preferences} if use_preferences_path else {}
        return OrganizerWindow(root, history_path=self.history, **options)

    def button(self, app, label):
        return self.find_button(app.root, label)

    def find_button(self, parent, label):
        search = [parent]
        while search:
            widget = search.pop()
            if isinstance(widget, ttk.Button) and widget.cget("text") == label:
                return widget
            search.extend(widget.winfo_children())
        self.fail(f"Missing button {label!r}")

    def wait_for_preview(self, app):
        deadline = monotonic() + 5
        while app.busy and monotonic() < deadline:
            app.root.update()
            sleep(0.01)
        self.assertFalse(app.busy)

    def test_custom_profile_requires_preview_and_restores_filters(self):
        self.button(self.app, "Zapisz profil…")
        app = self.window(use_preferences_path=True)
        source = self.folder / "Pobrane"
        source.mkdir()
        (source / "Pomiń").mkdir()
        (source / "old.txt").write_text("old", encoding="utf-8")
        (source / "new.txt").write_text("new", encoding="utf-8")
        (source / "Pomiń" / "ignored.txt").write_text("ignored", encoding="utf-8")
        for path in (source / "old.txt", source / "Pomiń" / "ignored.txt"):
            older = time() - 5 * 86400
            os.utime(path, (older, older))
        app.source.set(str(source))
        app.recursive.set(True)
        app.min_age.set("2")
        app.excluded_folders.set("Pomiń")
        ask = "organizer_ui.simpledialog.askstring"
        with patch(ask, return_value="Archiwizacja"):
            self.button(app, "Zapisz profil…").invoke()
        self.assertTrue(self.preferences.is_file())

        restored = self.window(use_preferences_path=True)
        restored.custom_profile.set("Archiwizacja")
        self.button(restored, "Wczytaj").invoke()
        self.assertTrue(restored.execute_button.instate(["disabled"]))
        restored.preview()
        self.wait_for_preview(restored)

        self.assertEqual([item.source.name for item in restored.plan], ["old.txt"])

    def test_saving_english_dark_preferences_restores_optional_paths(self):
        app = self.app
        source = self.folder / "source"
        source.mkdir()
        app.source.set(str(source))

        with patch.object(app.root, "report_callback_exception") as callback_error:
            self.button(app, "Ustawienia").invoke()
            callback_error.assert_not_called()
        dialog, = (widget for widget in app.root.winfo_children() if isinstance(widget, tk.Toplevel))
        self.assertEqual(dialog.title(), "Ustawienia")
        controls = dialog.winfo_children()[0].winfo_children()
        language, theme, conflict = (widget for widget in controls if isinstance(widget, ttk.Combobox))
        language.set("English")
        theme.set("Ciemny")
        conflict.set("Pomiń plik")
        next(widget for widget in controls if isinstance(widget, ttk.Checkbutton)).invoke()
        self.find_button(dialog, "Zapisz").invoke()
        self.assertFalse(dialog.winfo_exists())
        self.assertIn("po ponownym uruchomieniu", app.status.get())
        self.assertEqual(app.conflict.get(), "Pomiń plik")

        self.button(app, "Ustawienia").invoke()
        dialog, = (widget for widget in app.root.winfo_children() if isinstance(widget, tk.Toplevel))
        self.assertEqual(dialog.title(), "Ustawienia")
        language = next(widget for widget in dialog.winfo_children()[0].winfo_children()
                        if isinstance(widget, ttk.Combobox))
        self.assertEqual(language.get(), "English")
        self.find_button(dialog, "Anuluj").invoke()
        restored = self.window(use_preferences_path=True)
        self.assertEqual(restored.preview_button.cget("text"), "Show preview")
        self.assertEqual(restored.source.get(), str(source))
        self.assertEqual(restored.conflict.get(), "Skip file")
        red, green, blue = restored.root.winfo_rgb(ttk.Style(restored.root).lookup("Panel.TFrame", "background"))
        self.assertLess(max(red, green, blue), 32768)
        self.button(restored, "Settings").invoke()
        dialog, = (widget for widget in restored.root.winfo_children() if isinstance(widget, tk.Toplevel))
        self.assertEqual(dialog.title(), "Settings")


if __name__ == "__main__":
    unittest.main()
