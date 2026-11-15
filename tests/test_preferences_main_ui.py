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
        search = [app.root]
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
        self.button(self.app, "Ustawienia")
        from dataclasses import replace

        app = self.window(use_preferences_path=True)
        source = self.folder / "source"
        source.mkdir()
        app.source.set(str(source))
        self.assertTrue(app._apply_preferences(replace(app.preferences, language="en", theme="dark",
                                                       remember_folders=True, default_conflict="skip")))

        restored = self.window(use_preferences_path=True)
        self.assertEqual(restored.preview_button.cget("text"), "Show preview")
        self.assertEqual(restored.source.get(), str(source))
        self.assertEqual(restored.conflict.get(), "Skip file")
        red, green, blue = restored.root.winfo_rgb(ttk.Style(restored.root).lookup("Panel.TFrame", "background"))
        self.assertLess(max(red, green, blue), 32768)


if __name__ == "__main__":
    unittest.main()
