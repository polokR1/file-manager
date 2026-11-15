from dataclasses import replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app_preferences import Preferences, PreferencesStore, Profile


class PreferencesStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "PoloQP" / "FileOrganizer" / "settings.json"
        self.store = PreferencesStore(self.path)

    def test_default_path_uses_local_app_data(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}):
            self.assertEqual(PreferencesStore().path, self.path)

    def test_missing_file_returns_defaults_without_creating_file(self):
        self.assertEqual(self.store.load(), Preferences())
        self.assertFalse(self.path.parent.exists())

    def test_corrupt_file_returns_defaults_without_overwriting_file(self):
        invalid = b"{invalid json"
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(invalid)
        self.assertEqual(self.store.load(), Preferences())
        self.assertEqual(self.path.read_bytes(), invalid)

    def test_round_trips_unicode_paths_and_saved_profile(self):
        profile = Profile(r"C:\Zdjęcia", r"D:\Archiwum", ".jpg, .heic", True, "original", "move",
                          "skip", 30, "Szkice\nW koszu")
        prefs = Preferences("en", "dark", True, "skip", r"C:\Pobrane", r"D:\Zdjęcia",
                            {"Zdjęcia z telefonu": profile})
        self.store.save(prefs)
        self.assertEqual(self.store.load(), prefs)

    def test_disabled_remember_folders_omits_automatic_paths_but_keeps_profile_paths(self):
        profile = Profile(r"C:\Zdjęcia", r"D:\Archiwum", ".jpg", True, "created", "copy", "rename")
        prefs = Preferences(remember_folders=False, source=r"C:\Automatyczne", destination=r"D:\Automatyczne",
                            profiles={"Archiwum": profile})
        self.store.save(prefs)
        self.assertEqual(self.store.load(), replace(prefs, source="", destination=""))
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn("source", document)
        self.assertNotIn("destination", document)

    def test_partial_invalid_data_does_not_discard_valid_independent_options(self):
        self.path.parent.mkdir(parents=True)
        raw_profile = {
            "source": "Src", "destination": "Dst", "extensions": "*", "recursive": False,
            "grouping": "flat", "operation": "copy", "conflict": "rename",
        }
        self.path.write_text(json.dumps({
            "language": "en", "theme": ["unknown"], "remember_folders": "false", "default_conflict": "skip",
            "source": "not restored", "profiles": {
                " Files ": raw_profile,
                "FILES": dict(raw_profile, operation="move"),
                "Bad": dict(raw_profile, min_age_days=True),
            }
        }), encoding="utf-8")
        loaded = self.store.load()
        self.assertEqual(loaded, Preferences(language="en", default_conflict="skip",
                                             profiles={"Files": Profile(**raw_profile)}))

    def test_failed_replacement_preserves_old_file_and_removes_temporary_file(self):
        self.store.save(Preferences(language="pl"))
        with patch("app_preferences.os.replace", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                self.store.save(Preferences(language="en"))
        self.assertEqual(self.store.load().language, "pl")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_existing_directory_at_settings_path_is_reported(self):
        self.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.store.load()
