import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from app_preferences import Preferences, Profile
from organizer_preferences_ui import show_about, show_preferences
from app_version import APP_NAME, APP_VERSION


def widgets(parent, kind):
    for child in parent.winfo_children():
        if isinstance(child, kind):
            yield child
        yield from widgets(child, kind)


class PreferencesDialogTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"No desktop Tk environment: {error}")
        self.addCleanup(self.root.destroy)
        self.root.withdraw()

    def button(self, parent, text):
        return next(child for child in widgets(parent, ttk.Button) if child.cget("text") == text)

    def test_save_updates_choices_without_losing_profiles_or_paths(self):
        profile = Profile("source", "destination", "*", True, "category", "copy", "rename")
        initial = Preferences(source="folder-x", destination="folder-y", profiles={"Documents": profile})
        saved = []
        dialog = show_preferences(self.root, initial, saved.append)
        language, theme, conflict = list(widgets(dialog, ttk.Combobox))
        language.current(1)
        theme.current(2)
        conflict.current(1)
        next(widgets(dialog, ttk.Checkbutton)).invoke()
        self.button(dialog, "Zapisz").invoke()
        self.assertEqual(saved, [Preferences("en", "dark", True, "skip", "folder-x", "folder-y",
                                             {"Documents": profile})])
        self.assertFalse(dialog.winfo_exists())

    def test_cancel_does_not_submit_settings(self):
        dialog = show_preferences(self.root, Preferences(language="en"), self.fail)
        self.button(dialog, "Cancel").invoke()
        self.assertFalse(dialog.winfo_exists())

    def test_settings_stay_open_if_save_is_rejected(self):
        dialog = show_preferences(self.root, Preferences(language="en"), lambda new: False)
        self.button(dialog, "Save").invoke()
        self.assertTrue(dialog.winfo_exists())

    def test_about_shows_the_version_and_public_links_in_the_requested_language(self):
        dialog = show_about(self.root, "pl")
        labels = tuple(child.cget("text") for child in widgets(dialog, ttk.Label))
        self.assertIn(APP_NAME, labels)
        self.assertTrue(any(APP_VERSION in label for label in labels))
        self.assertTrue(any("PoloQP" in label for label in labels))
        self.assertTrue(any("własne profile" in label for label in labels))
        with patch("organizer_preferences_ui.webbrowser.open", return_value=True) as open_url:
            self.button(dialog, "Polityka prywatności").invoke()
            self.button(dialog, "contact@poloqp.com").invoke()
            self.button(dialog, "poloqp.com").invoke()
        self.assertEqual([call.args[0] for call in open_url.call_args_list],
                         ["https://poloqp.com/pl/apps/file-organizer/privacy/", "mailto:contact@poloqp.com",
                          "https://poloqp.com/"])
        dialog.destroy()
        english = show_about(self.root, "en")
        with patch("organizer_preferences_ui.webbrowser.open", return_value=True) as open_url:
            self.button(english, "Privacy policy").invoke()
        self.assertEqual(open_url.call_args.args[0], "https://poloqp.com/apps/file-organizer/privacy/")

    def test_about_copies_an_allowlisted_diagnostic_report(self):
        dialog = show_about(self.root, "en")
        with patch.object(dialog, "clipboard_clear"), patch.object(dialog, "clipboard_append") as append:
            self.button(dialog, "Copy diagnostics").invoke()
        report = append.call_args.args[0]
        self.assertIn(f"{APP_NAME} {APP_VERSION}", report)
        self.assertNotIn(str(Path.home()), report)
        self.assertNotIn(str(Path.cwd()), report)
        self.assertNotIn(Path.home().name, report)
        self.assertIn("Windows" if os.name == "nt" else "System", report)
