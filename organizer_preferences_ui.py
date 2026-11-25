"""Dialogs for preferences, privacy information and basic diagnostics."""

from dataclasses import replace
import platform
import sys
import tkinter as tk
from tkinter import ttk
import webbrowser

from app_preferences import CONFLICTS, LANGUAGES, THEMES, Preferences
from app_version import APP_NAME, APP_VERSION


PUBLISHER = "PoloQP"
SUPPORT_EMAIL = "contact@poloqp.com"
PUBLISHER_URL = "https://poloqp.com/"
PRIVACY_URLS = {
    "pl": "https://poloqp.com/pl/apps/file-organizer/privacy/",
    "en": "https://poloqp.com/apps/file-organizer/privacy/",
}
TEXT = {
    "pl": {
        "settings": "Ustawienia", "language": "Język", "theme": "Motyw",
        "remember": "Zapamiętuj ostatnio używane foldery",
        "folder_note": "Zapisane profile zachowują własne ścieżki.",
        "conflict": "Gdy nazwa pliku jest zajęta",
        "themes": ("Systemowy", "Jasny", "Ciemny"),
        "conflicts": ("Zmień nazwę", "Pomiń plik"),
        "save": "Zapisz", "cancel": "Anuluj", "close": "Zamknij",
        "about": "O programie", "version": "Wersja", "publisher": "Wydawca",
        "privacy": "Polityka prywatności",
        "changes": "Wersja 1.3 wprowadza język polski i angielski, motywy, własne profile, ostrzeżenia "
                   "podczas podglądu i ponawianie nieudanych operacji.",
        "copy": "Skopiuj diagnostykę", "copied": "Skopiowano informacje diagnostyczne.",
        "copy_note": "Diagnostyka obejmuje wersję programu, system i architekturę.",
        "url_error": "Nie udało się otworzyć odnośnika.",
    },
    "en": {
        "settings": "Settings", "language": "Language", "theme": "Theme",
        "remember": "Remember the most recently used folders",
        "folder_note": "Saved profiles keep their own paths.",
        "conflict": "When a file name is already in use",
        "themes": ("System", "Light", "Dark"),
        "conflicts": ("Rename", "Skip file"),
        "save": "Save", "cancel": "Cancel", "close": "Close",
        "about": "About", "version": "Version", "publisher": "Publisher",
        "privacy": "Privacy policy",
        "changes": "Version 1.3 adds Polish and English, themes, custom profiles, preview warnings, and retries "
                   "for unsuccessful operations.",
        "copy": "Copy diagnostics", "copied": "Diagnostics copied.",
        "copy_note": "Diagnostics include the app version, system and architecture.",
        "url_error": "Could not open the link.",
    },
}


def _dialog(parent, title):
    window = tk.Toplevel(parent)
    window.title(title)
    window.transient(parent.winfo_toplevel())
    window.resizable(False, False)
    window.bind("<Escape>", lambda _event: window.destroy())
    frame = ttk.Frame(window, padding=18, style="Panel.TFrame")
    frame.pack(fill="both", expand=True)
    return window, frame


def _show_dialog(window, focus):
    window.update_idletasks()
    window.grab_set()
    focus.focus_set()
    return window


def show_preferences(parent, prefs: Preferences, apply, *, language: str | None = None):
    strings = TEXT.get(prefs.language if language is None else language, TEXT["pl"])
    window, frame = _dialog(parent, strings["settings"])

    def choice(row, label, titles, keys, current):
        ttk.Label(frame, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w",
                                                                padx=(0, 12), pady=5)
        box = ttk.Combobox(frame, state="readonly", values=titles, width=21)
        box.grid(row=row, column=1, sticky="ew", pady=5)
        box.current(keys.index(current) if current in keys else 0)
        return box

    language_box = choice(0, strings["language"], ("Polski", "English"), LANGUAGES, prefs.language)
    theme = choice(1, strings["theme"], strings["themes"], THEMES, prefs.theme)
    conflict = choice(2, strings["conflict"], strings["conflicts"], CONFLICTS, prefs.default_conflict)
    remember = tk.BooleanVar(window, value=prefs.remember_folders)
    ttk.Checkbutton(frame, text=strings["remember"], variable=remember).grid(
        row=3, column=0, columnspan=2, sticky="w", pady=(8, 2))
    ttk.Label(frame, text=strings["folder_note"], style="Help.TLabel").grid(
        row=4, column=0, columnspan=2, sticky="w", padx=(20, 0))

    def save():
        updated = replace(prefs, language=LANGUAGES[language_box.current()], theme=THEMES[theme.current()],
                          remember_folders=remember.get(), default_conflict=CONFLICTS[conflict.current()])
        if apply(updated) is not False and window.winfo_exists():
            window.destroy()

    actions = ttk.Frame(frame, style="Panel.TFrame")
    actions.grid(row=5, column=0, columnspan=2, sticky="e", pady=(18, 0))
    ttk.Button(actions, text=strings["cancel"], command=window.destroy).pack(side="left", padx=(0, 6))
    ttk.Button(actions, text=strings["save"], style="Accent.TButton", command=save).pack(side="left")
    return _show_dialog(window, language_box)


def _diagnostics() -> str:
    if hasattr(sys, "getwindowsversion"):
        version = sys.getwindowsversion()
        system = f"Windows {version.major}.{version.minor}.{version.build}"
    else:
        system = sys.platform
    hardware = platform.machine().casefold()
    architectures = {"amd64": "x64", "x86_64": "x64", "x86": "x86", "i386": "x86", "i686": "x86",
                     "arm64": "ARM64", "aarch64": "ARM64"}
    return f"{APP_NAME} {APP_VERSION}\nSystem: {system}\nArchitecture: {architectures.get(hardware, 'unknown')}"


def show_about(parent, language: str):
    strings = TEXT.get(language, TEXT["pl"])
    window, frame = _dialog(parent, strings["about"])
    ttk.Label(frame, text=APP_NAME, style="Section.TLabel").pack(anchor="w")
    ttk.Label(frame, text=f"{strings['version']} {APP_VERSION}  ·  {strings['publisher']} {PUBLISHER}",
              style="Panel.TLabel").pack(anchor="w", pady=(4, 12))
    ttk.Label(frame, text=strings["changes"], wraplength=390, justify="left",
              style="Panel.TLabel").pack(anchor="w", pady=(0, 12))
    links = ttk.Frame(frame, style="Panel.TFrame")
    links.pack(fill="x")
    status = tk.StringVar(window, value=strings["copy_note"])

    def open_url(url):
        try:
            if webbrowser.open(url):
                return
        except OSError:
            pass
        status.set(f"{strings['url_error']} {url.removeprefix('mailto:')}")

    def copy_diagnostics():
        window.clipboard_clear()
        window.clipboard_append(_diagnostics())
        status.set(strings["copied"])

    ttk.Button(links, text=strings["privacy"],
               command=lambda: open_url(PRIVACY_URLS.get(language, PRIVACY_URLS["pl"]))).pack(side="left")
    ttk.Button(links, text=SUPPORT_EMAIL,
               command=lambda: open_url(f"mailto:{SUPPORT_EMAIL}")).pack(side="left", padx=(6, 0))
    ttk.Button(links, text="poloqp.com", command=lambda: open_url(PUBLISHER_URL)).pack(side="left", padx=(6, 0))
    ttk.Label(frame, textvariable=status, wraplength=365, style="Help.TLabel").pack(anchor="w", pady=(14, 0))
    actions = ttk.Frame(frame, style="Panel.TFrame")
    actions.pack(fill="x", pady=(12, 0))
    ttk.Button(actions, text=strings["copy"], command=copy_diagnostics).pack(side="left")
    close = ttk.Button(actions, text=strings["close"], command=window.destroy)
    close.pack(side="right", padx=(16, 0))
    return _show_dialog(window, close)
