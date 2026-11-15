"""User settings and named organizing profiles."""

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


LANGUAGES = ("pl", "en")
THEMES = ("system", "light", "dark")
CONFLICTS = ("rename", "skip")
GROUPINGS = ("category", "extension", "flat", "original", "created", "modified", "size")
OPERATIONS = ("copy", "move")
MAX_SETTINGS_BYTES = 1024 * 1024
MAX_AGE_DAYS = 365_000


@dataclass(frozen=True)
class Profile:
    source: str
    destination: str
    extensions: str
    recursive: bool
    grouping: str
    operation: str
    conflict: str
    min_age_days: int = 0
    excluded_folders: str = ""


@dataclass(frozen=True)
class Preferences:
    language: str = "pl"
    theme: str = "system"
    remember_folders: bool = False
    default_conflict: str = "rename"
    source: str = ""
    destination: str = ""
    profiles: dict[str, Profile] = field(default_factory=dict)


def _read_profile(raw) -> Profile | None:
    if not isinstance(raw, dict):
        return None
    source = raw.get("source")
    destination = raw.get("destination")
    extensions = raw.get("extensions")
    excluded_folders = raw.get("excluded_folders", "")
    recursive = raw.get("recursive")
    grouping = raw.get("grouping")
    operation = raw.get("operation")
    conflict = raw.get("conflict")
    min_age_days = raw.get("min_age_days", 0)
    if (not all(isinstance(text, str) and "\0" not in text
                for text in (source, destination, extensions, excluded_folders))
            or type(recursive) is not bool or grouping not in GROUPINGS
            or operation not in OPERATIONS or conflict not in CONFLICTS
            or type(min_age_days) is not int or not 0 <= min_age_days <= MAX_AGE_DAYS):
        return None
    return Profile(source, destination, extensions, recursive, grouping, operation, conflict, min_age_days,
                   excluded_folders)


def _decode_preferences(raw) -> Preferences:
    if not isinstance(raw, dict):
        return Preferences()

    def choice(key: str, choices: tuple[str, ...]) -> str:
        value = raw.get(key)
        return value if value in choices else choices[0]

    def remembered_path(key: str) -> str:
        value = raw.get(key)
        return value if remember_folders and isinstance(value, str) and "\0" not in value else ""

    profiles = {}
    names = set()
    listed = raw.get("profiles")
    if isinstance(listed, dict):
        for label, item in listed.items():
            name = label.strip() if isinstance(label, str) else ""
            if not name or "\0" in name or name.casefold() in names:
                continue
            profile = _read_profile(item)
            if profile is not None:
                names.add(name.casefold())
                profiles[name] = profile

    remember_folders = raw.get("remember_folders") is True
    return Preferences(choice("language", LANGUAGES), choice("theme", THEMES), remember_folders,
                       choice("default_conflict", CONFLICTS), remembered_path("source"),
                       remembered_path("destination"), profiles)


class PreferencesStore:
    def __init__(self, path: Path | None = None):
        if path is None:
            root = os.environ.get("LOCALAPPDATA")
            if root:
                directory = Path(root)
            elif os.name == "nt":
                directory = Path.home() / "AppData" / "Local"
            else:
                directory = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
            path = directory / "PoloQP" / "FileOrganizer" / "settings.json"
        self.path = Path(path)

    def load(self) -> Preferences:
        try:
            with self.path.open("rb") as file:
                data = file.read(MAX_SETTINGS_BYTES + 1)
        except FileNotFoundError:
            return Preferences()
        if len(data) > MAX_SETTINGS_BYTES:
            return Preferences()
        try:
            return _decode_preferences(json.loads(data.decode("utf-8-sig")))
        except (ValueError, RecursionError):
            return Preferences()

    def save(self, preferences: Preferences) -> None:
        data = asdict(_decode_preferences(asdict(preferences)))
        if not data["remember_folders"]:
            del data["source"], data["destination"]
        encoded = (json.dumps(data, ensure_ascii=True, indent=2) + "\n").encode("utf-8")
        if len(encoded) > MAX_SETTINGS_BYTES:
            raise OSError("Settings exceed the maximum supported size.")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode="wb", prefix=".settings-", suffix=".tmp",
                                    dir=self.path.parent, delete=False) as file:
                temporary = Path(file.name)
                file.write(encoded)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
