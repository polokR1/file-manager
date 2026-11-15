from dataclasses import replace
import os
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic
import tkinter as tk
from tkinter import filedialog, font, messagebox, simpledialog, ttk

from app_preferences import MAX_AGE_DAYS, Preferences, PreferencesStore, Profile
from app_version import APP_NAME
from duplicate_cleanup import recycle_duplicates as recycle_duplicate_files
from file_analysis import AnalysisCancelled, analyze_files
from operation_feedback import OperationIssue, check_destination, describe_issue
from organizer import CATEGORIES, Transfer, build_plan, parse_extensions
from organizer_history_ui import HistoryPanel
from organizer_operation_ui import show_preview_issues
from organizer_preferences_ui import show_about, show_preferences
from organizer_report_ui import ReportPanel
from transfer_history import HistoryError, TransferHistory


GROUPING_TEXT = {
    "category": ("Kategorie", "Categories"),
    "extension": ("Rozszerzenia", "Extensions"),
    "flat": ("Jeden folder", "One folder"),
    "original": ("Data oryginalna", "Original date"),
    "created": ("Data utworzenia w Windows", "Windows creation date"),
    "modified": ("Data modyfikacji", "Modification date"),
    "size": ("Rozmiar", "Size"),
}
DATE_GROUPING_HELP = {
    "original": ("Rok i miesiąc z metadanych. Brak daty oznacza folder „Bez daty w metadanych”.",
                 "Year and month from metadata. Files without a date go to “Bez daty w metadanych”."),
    "created": ("Rok i miesiąc według daty utworzenia pliku w Windows.",
                "Year and month from the Windows file creation date."),
    "modified": ("Rok i miesiąc według daty ostatniej modyfikacji pliku.",
                 "Year and month from the file’s last modification date."),
}
OPERATION_TEXT = {"copy": ("Kopiuj", "Copy"), "move": ("Przenieś", "Move")}
CONFLICT_TEXT = {"rename": ("Zmień nazwę", "Rename"), "skip": ("Pomiń plik", "Skip file")}
GROUPINGS = tuple(labels[0] for labels in GROUPING_TEXT.values())
OPERATIONS = tuple(labels[0] for labels in OPERATION_TEXT.values())
CONFLICT_OPTIONS = tuple(labels[0] for labels in CONFLICT_TEXT.values())
PREVIEW_LIMIT = 500
PREVIEW_PAGE_SIZE = 200


def system_uses_dark_theme() -> bool:
    if os.name != "nt":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            light, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
    except OSError:
        return False
    return light == 0


class OrganizerWindow:
    def __init__(self, root: tk.Tk, *, history_path: Path | None = None, preferences_path: Path | None = None):
        self.root = root
        self.history = TransferHistory(history_path)
        if preferences_path is None and history_path is not None:
            preferences_path = history_path.with_name("preferences.json")
        self.preferences_store = PreferencesStore(preferences_path)
        settings_error = None
        try:
            self.preferences = self.preferences_store.load()
        except OSError as error:
            self.preferences = Preferences()
            settings_error = error
        self.language = self.preferences.language
        self.groups = {key: self._t(*values) for key, values in GROUPING_TEXT.items()}
        self.operations = {key: self._t(*values) for key, values in OPERATION_TEXT.items()}
        self.conflicts = {key: self._t(*values) for key, values in CONFLICT_TEXT.items()}
        remember = self.preferences.remember_folders
        self.source = tk.StringVar(root, self.preferences.source if remember else "")
        self.destination = tk.StringVar(root, self.preferences.destination if remember else "")
        self.extensions = tk.StringVar(root, "*")
        self.min_age = tk.StringVar(root, "0")
        self.excluded_folders = tk.StringVar(root)
        self.custom_profile = tk.StringVar(root)
        self.recursive = tk.BooleanVar(root, False)
        self.grouping = tk.StringVar(root, self.groups["category"])
        self.operation = tk.StringVar(root, self.operations["copy"])
        self.conflict = tk.StringVar(root, self.conflicts[self.preferences.default_conflict])
        self.summary = tk.StringVar(root)
        self.status = tk.StringVar(root, "")
        self.plan: list[Transfer] = []
        self._preview_issues: list[OperationIssue] = []
        self._failed_transfers: tuple[Transfer, ...] = ()
        self._retry_move = False
        self._excluded: set[Transfer] = set()
        self._page = 0
        self._renamed = 0
        self.page_label = tk.StringVar(root)
        self.busy = ""
        self._manual_destination = bool(self.destination.get()) and (
            self.destination.get() != str(Path(self.source.get()) / "Uporządkowane")
        )
        self._suggesting_destination = False
        self._paths: tuple[Path, Path] | None = None
        self._last_destination: Path | None = None
        self._details: dict[str, tuple[Transfer, str]] = {}
        self._controls = []
        self._events = Queue()
        self._stop = Event()

        root.title(APP_NAME)
        root.geometry("940x790")
        root.minsize(740, 680)
        icon = Path(__file__).with_name("assets") / "file-organizer.ico"
        if icon.is_file():
            try:
                root.iconbitmap(default=str(icon))
            except tk.TclError:
                pass
        root.protocol("WM_DELETE_WINDOW", self._close)
        self._set_styles()
        self._build_layout()
        self.source.trace_add("write", self._source_changed)
        self.destination.trace_add("write", self._destination_changed)
        for variable in (self.extensions, self.recursive):
            variable.trace_add("write", self._scope_changed)
        self.grouping.trace_add("write", self._grouping_changed)
        for variable in (self.operation, self.conflict, self.min_age, self.excluded_folders):
            variable.trace_add("write", self._invalidate)
        self.custom_profile.trace_add("write", self._profile_selection_changed)
        if self.source.get() and not self.destination.get():
            self._source_changed()
        else:
            self._invalidate()
        if settings_error:
            self.status.set(self._t("Nie udało się odczytać ustawień.", "Could not read preferences."))

    def _t(self, polish, english, **fields):
        text = english if self.language == "en" else polish
        return text.format(**fields) if fields else text

    @staticmethod
    def _selected_key(labels, variable):
        return next(key for key, title in labels.items() if title == variable.get())

    def _set_styles(self):
        theme = self.preferences.theme
        dark = theme == "dark" or (theme == "system" and system_uses_dark_theme())
        if dark:
            background, panel, text, secondary = "#161d27", "#222c3a", "#f4f7fb", "#b9c8dc"
            field, surface, heading, border, alternate = "#1a2431", "#304258", "#303f54", "#51637b", "#273344"
            active, pressed, muted = "#3b536f", "#293a50", "#2e3c4f"
        else:
            background, panel, text, secondary = "#eff3f8", "#ffffff", "#18283d", "#485b73"
            field, surface, heading, border, alternate = "#ffffff", "#eaf1fa", "#e4edf8", "#b7c7db", "#f4f7fb"
            active, pressed, muted = "#dceafa", "#c6dcf5", "#e0e7ef"
        accent = "#1958b7"
        self._alternate_row_color = alternate
        self.root.configure(background=background)
        self.root.option_add("*TCombobox*Listbox.background", field)
        self.root.option_add("*TCombobox*Listbox.foreground", text)
        self.root.option_add("*TCombobox*Listbox.selectBackground", accent)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "white")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        family = "Inter" if "Inter" in font.families(self.root) else "Segoe UI"
        font.nametofont("TkDefaultFont", root=self.root).configure(family=family, size=10)
        font.nametofont("TkTextFont", root=self.root).configure(family=family, size=10)

        style.configure(".", background=panel, foreground=text, bordercolor=border,
                        lightcolor=panel, darkcolor=panel, troughcolor=field, focuscolor=accent)
        style.configure("App.TFrame", background=background)
        style.configure("Panel.TFrame", background=panel)
        style.configure("Title.TLabel", background=background, foreground=text, font=(family, 22, "bold"))
        style.configure("Subtitle.TLabel", background=background, foreground=secondary)
        style.configure("Section.TLabel", background=panel, foreground=text, font=(family, 11, "bold"))
        style.configure("Panel.TLabel", background=panel, foreground=text)
        style.configure("Help.TLabel", background=panel, foreground=secondary)
        style.configure("Panel.TCheckbutton", background=panel, foreground=text, indicatorbackground=field)
        style.map("Panel.TCheckbutton", background=[("active", panel)], foreground=[("disabled", secondary)])
        style.configure("TButton", padding=(12, 6), background=surface, foreground=text)
        style.configure("Small.TButton", padding=(8, 4))
        style.map("TButton", foreground=[("disabled", secondary)],
                  background=[("disabled", muted), ("pressed", pressed), ("active", active)])
        style.configure("Accent.TButton", foreground="white", background=accent, bordercolor=accent)
        style.map("Accent.TButton", foreground=[("disabled", secondary), ("!disabled", "white")],
                  background=[("disabled", muted), ("pressed", "#10418e"), ("active", "#104caa")])
        style.configure("TEntry", fieldbackground=field, foreground=text, insertcolor=text)
        style.configure("TCombobox", fieldbackground=field, background=surface, foreground=text, arrowcolor=text)
        style.map("TCombobox", fieldbackground=[("readonly", field), ("disabled", muted)],
                  foreground=[("disabled", secondary), ("readonly", text)])
        style.configure("Treeview", background=panel, fieldbackground=panel, foreground=text,
                        rowheight=max(25, font.nametofont("TkDefaultFont", root=self.root).metrics("linespace") + 10))
        style.configure("Treeview.Heading", foreground=text, background=heading, font=(family, 10, "bold"))
        style.map("Treeview", background=[("selected", accent)], foreground=[("selected", "white")])
        style.map("Treeview.Heading", background=[("active", active)])
        style.configure("TNotebook", background=background)
        style.configure("TNotebook.Tab", padding=(12, 5), background=surface, foreground=text)
        style.map("TNotebook.Tab", background=[("selected", panel), ("active", active)],
                  foreground=[("disabled", secondary)])
        style.configure("TScrollbar", background=surface, arrowcolor=text)
        style.configure("TProgressbar", background=accent, troughcolor=field)

    def _build_layout(self):
        app = ttk.Frame(self.root, style="App.TFrame", padding=(22, 15))
        app.pack(fill="both", expand=True)
        app.columnconfigure(0, weight=1)
        app.rowconfigure(2, weight=1)

        header = ttk.Frame(app, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        about = ttk.Button(header, text=self._t("O programie", "About"), style="Small.TButton",
                           command=lambda: show_about(self.root, self.language))
        about.grid(row=0, column=2, padx=(6, 0))
        preferences = ttk.Button(header, text=self._t("Ustawienia", "Settings"), style="Small.TButton",
                                 command=self._open_preferences)
        preferences.grid(row=0, column=1)
        self._controls.extend((preferences, about))
        ttk.Label(header, text=self._t("Sprawdź zawartość folderu, znajdź duplikaty lub uporządkuj pliki.",
                                       "Analyze a folder, find duplicates or organize files."),
                  style="Subtitle.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(3, 0))

        settings = ttk.Frame(app, style="Panel.TFrame", padding=(14, 10))
        settings.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        settings.columnconfigure(1, weight=1)
        ttk.Label(settings, text=self._t("Foldery", "Folders"), style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 6))
        folders = ((1, self._t("Źródło", "Source"), self.source), (2, self._t("Cel", "Destination"), self.destination))
        for row, title, variable in folders:
            ttk.Label(settings, text=title, style="Panel.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 14))
            entry = ttk.Entry(settings, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew", pady=3)
            browse = ttk.Button(settings, text=self._t("Wybierz…", "Browse…"),
                                command=lambda field=variable: self._browse(field))
            browse.grid(row=row, column=2, padx=(8, 0), pady=3)
            self._controls.extend((entry, browse))

        scope = ttk.Frame(settings, style="Panel.TFrame")
        scope.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        ttk.Label(scope, text=self._t("Rozszerzenia", "Extensions"), style="Panel.TLabel").pack(side="left", padx=(0, 6))
        extension_field = ttk.Entry(scope, textvariable=self.extensions, width=12)
        extension_field.pack(side="left", padx=(0, 7))
        ttk.Label(scope, text=self._t("Wszystkie pliki (*) lub np. jpg, pdf", "All files (*) or e.g. jpg, pdf"),
                  style="Help.TLabel").pack(side="left")
        include_subdirs = ttk.Checkbutton(scope, text=self._t("Uwzględnij podfoldery", "Include subfolders"),
                                          variable=self.recursive,
                                          style="Panel.TCheckbutton")
        include_subdirs.pack(side="right")
        self._controls.extend((extension_field, include_subdirs))

        self.tabs = ttk.Notebook(app)
        self.tabs.grid(row=2, column=0, sticky="nsew")
        sort_tab = ttk.Frame(self.tabs, style="Panel.TFrame", padding=(14, 10))
        sort_tab.columnconfigure(0, weight=1)
        sort_tab.rowconfigure(1, weight=1)
        self.tabs.add(sort_tab, text=self._t("Porządkowanie", "Organize"))
        self.report = ReportPanel(self.tabs, self.analyze, self._cancel, recycle=self.recycle_duplicates,
                                  language=self.language)
        self.tabs.add(self.report, text=self._t("Analiza i duplikaty", "Analysis and duplicates"))
        self.history_panel = HistoryPanel(self.tabs, self.undo_history, language=self.language)
        self.tabs.add(self.history_panel, text=self._t("Historia", "History"))
        self.tabs.bind("<<NotebookTabChanged>>", self._history_tab_selected)

        options = ttk.Frame(sort_tab, style="Panel.TFrame")
        options.grid(row=0, column=0, sticky="ew", pady=(0, 9))
        profiles = ttk.Frame(options, style="Panel.TFrame")
        profiles.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        ttk.Label(profiles, text=self._t("Profile", "Profiles"), style="Section.TLabel").pack(side="left", padx=(0, 10))
        presets = ((self._t("Pobrane według kategorii", "Downloads by category"), False),
                   (self._t("Zdjęcia według daty", "Photos by date"), True))
        for label, photos in presets:
            preset = ttk.Button(profiles, text=label, style="Small.TButton",
                                command=lambda for_photos=photos: self._apply_profile(for_photos))
            preset.pack(side="left", padx=(0, 7))
            self._controls.append(preset)

        saved = ttk.Frame(options, style="Panel.TFrame")
        saved.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        ttk.Label(saved, text=self._t("Własne", "Custom"), style="Panel.TLabel").pack(side="left", padx=(0, 7))
        self.profile_box = ttk.Combobox(saved, state="readonly", textvariable=self.custom_profile, width=22,
                                       values=sorted(self.preferences.profiles, key=str.casefold))
        self.profile_box.pack(side="left", fill="x", expand=True)
        self.load_profile_button = ttk.Button(saved, text=self._t("Wczytaj", "Load"), style="Small.TButton",
                                              command=self._load_custom_profile)
        self.remove_profile_button = ttk.Button(saved, text=self._t("Usuń profil", "Delete profile"), style="Small.TButton",
                                                command=self._remove_custom_profile)
        save_profile = ttk.Button(saved, text=self._t("Zapisz profil…", "Save profile…"), style="Small.TButton",
                                  command=self._save_custom_profile)
        for button in (self.load_profile_button, save_profile, self.remove_profile_button):
            button.pack(side="left", padx=(6, 0))
        self._controls.extend((self.profile_box, save_profile))

        for column in range(3):
            options.columnconfigure(column, weight=1, uniform="options")
        for column, label, variable, values in (
            (0, self._t("Operacja", "Operation"), self.operation, tuple(self.operations.values())),
            (1, self._t("Grupowanie", "Group by"), self.grouping, tuple(self.groups.values())),
            (2, self._t("Gdy nazwa jest zajęta", "If the name is taken"), self.conflict, tuple(self.conflicts.values())),
        ):
            field = ttk.Frame(options, style="Panel.TFrame")
            field.grid(row=2, column=column, sticky="ew", padx=(0, 12) if column < 2 else 0)
            ttk.Label(field, text=label, style="Panel.TLabel").pack(anchor="w", pady=(0, 4))
            widget = ttk.Combobox(field, state="readonly", textvariable=variable, values=values, width=14)
            if variable is self.grouping:
                self.grouping_box = widget
            widget.pack(fill="x")
            self._controls.append(widget)

        self.grouping_help = ttk.Label(options, style="Help.TLabel", wraplength=620)
        self.grouping_help.grid(row=3, column=0, columnspan=3, sticky="w", pady=(5, 0))
        self.grouping_help.grid_remove()

        filters = ttk.Frame(options, style="Panel.TFrame")
        filters.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(filters, text=self._t("Dni od modyfikacji (min.)", "Days since modification (min.)"),
                  style="Panel.TLabel").pack(side="left", padx=(0, 6))
        age = ttk.Entry(filters, textvariable=self.min_age, width=5)
        age.pack(side="left", padx=(0, 15))
        ttk.Label(filters, text=self._t("Pomiń foldery (oddziel ;)", "Skip folders (separate with ;)"),
                  style="Panel.TLabel").pack(side="left", padx=(0, 6))
        excluded = ttk.Entry(filters, textvariable=self.excluded_folders, width=13)
        excluded.pack(side="left", fill="x", expand=True)
        self._controls.extend((age, excluded))

        preview = ttk.Frame(sort_tab, style="Panel.TFrame")
        preview.grid(row=1, column=0, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(3, weight=1)
        preview_header = ttk.Frame(preview, style="Panel.TFrame")
        preview_header.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.preview_title = ttk.Label(preview_header, text=self._t("Podgląd", "Preview"), style="Section.TLabel")
        self.preview_title.pack(side="left")
        self.retry_button = ttk.Button(preview_header, text=self._t("Ponów nieudane", "Retry failed"),
                                       style="Small.TButton", command=lambda: self.retry_failed())
        self.retry_button.state(["disabled"])
        self.retry_button.pack(side="right")
        self.preview_issues_button = ttk.Button(
            preview_header, style="Small.TButton", text=self._t("Pokaż problemy", "Show issues"),
            command=lambda: show_preview_issues(self.root, self._preview_issues, self.language),
        )
        self.preview_issues_button.pack(side="right", padx=(0, 6))
        ttk.Label(preview, textvariable=self.summary, style="Help.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 7))
        actions = ttk.Frame(preview, style="Panel.TFrame")
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.select_all = ttk.Button(actions, text=self._t("Zaznacz wszystkie", "Select all"), style="Small.TButton",
                                     command=lambda: self._set_selection(True))
        self.select_none = ttk.Button(actions, text=self._t("Odznacz wszystkie", "Deselect all"), style="Small.TButton",
                                      command=lambda: self._set_selection(False))
        self.select_all.pack(side="left", padx=(0, 5))
        self.select_none.pack(side="left")
        self.next_page = ttk.Button(actions, text=self._t("Następna", "Next"), style="Small.TButton",
                                    command=lambda: self._change_page(1))
        self.prev_page = ttk.Button(actions, text=self._t("Poprzednia", "Previous"), style="Small.TButton",
                                    command=lambda: self._change_page(-1))
        self.next_page.pack(side="right")
        ttk.Label(actions, textvariable=self.page_label, style="Help.TLabel", width=10,
                  anchor="center").pack(side="right")
        self.prev_page.pack(side="right")
        self.table = ttk.Treeview(preview, columns=("include", "source", "target", "note"), show="headings",
                                  height=4, selectmode="browse")
        self.table.heading("include", text=self._t("Uwzględnij", "Include"), anchor="center")
        self.table.column("include", width=95, minwidth=95, stretch=False, anchor="center")
        for name, label, width in (("source", self._t("Plik źródłowy", "Source file"), 285),
                                   ("target", self._t("Plik docelowy", "Destination file"), 285),
                                   ("note", self._t("Uwagi", "Notes"), 120)):
            self.table.heading(name, text=label, anchor="w")
            self.table.column(name, width=width, minwidth=100, stretch=name != "note")
        self.table.tag_configure("alternate", background=self._alternate_row_color)
        self.table.bind("<Button-1>", self._toggle_click)
        self.table.bind("<space>", self._toggle_focused)
        self.table.bind("<Double-1>", self._show_details)
        vertical = ttk.Scrollbar(preview, orient="vertical", command=self.table.yview)
        horizontal = ttk.Scrollbar(preview, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.table.grid(row=3, column=0, sticky="nsew")
        vertical.grid(row=3, column=1, sticky="ns")
        horizontal.grid(row=4, column=0, sticky="ew")

        footer = ttk.Frame(sort_tab, style="Panel.TFrame")
        footer.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(footer)
        self.progress.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 7))
        ttk.Label(footer, textvariable=self.status, style="Help.TLabel", wraplength=270).grid(
            row=1, column=0, sticky="w")
        self.open_button = ttk.Button(footer, text=self._t("Otwórz folder docelowy", "Open destination"),
                                      command=self._open_destination)
        self.open_button.grid(row=1, column=1, padx=(8, 0))
        self.preview_button = ttk.Button(footer, text=self._t("Pokaż podgląd", "Show preview"), command=self.preview)
        self.preview_button.grid(row=1, column=2, padx=(8, 0))
        self.execute_button = ttk.Button(footer, style="Accent.TButton", command=self.execute)
        self.execute_button.grid(row=1, column=3, padx=(8, 0))

    @staticmethod
    def _read_path(variable: tk.StringVar) -> Path | None:
        value = variable.get().strip().strip('"')
        return Path(value) if value else None

    def _browse(self, variable: tk.StringVar):
        current = self._read_path(variable)
        initial = current if current is not None and current.is_dir() else None
        if initial is None and variable is self.destination:
            source = self._read_path(self.source)
            if source is not None and source.is_dir():
                initial = source
        label = (self._t("Wybierz folder źródłowy", "Choose a source folder") if variable is self.source else
                 self._t("Wybierz folder docelowy", "Choose a destination folder"))
        selected = filedialog.askdirectory(parent=self.root, title=label,
                                           initialdir=initial or Path.home(), mustexist=variable is self.source)
        if selected:
            variable.set(selected)

    def _source_changed(self, *_):
        if not self._manual_destination:
            source = self._read_path(self.source)
            self._suggesting_destination = True
            self.destination.set(str(source / "Uporządkowane") if source and source.is_absolute() else "")
            self._suggesting_destination = False
        self._scope_changed()

    def _destination_changed(self, *_):
        if not self._suggesting_destination:
            self._manual_destination = bool(self.destination.get().strip())
            self._scope_changed()

    def _open_preferences(self):
        show_preferences(self.root, self.preferences, self._apply_preferences, language=self.language)

    def _current_folder_preferences(self, preferences: Preferences) -> Preferences:
        def absolute_text(variable):
            path = self._read_path(variable)
            return str(path) if preferences.remember_folders and path is not None and path.is_absolute() else ""

        return replace(preferences, source=absolute_text(self.source), destination=absolute_text(self.destination))

    def _write_preferences(self, preferences: Preferences) -> bool:
        try:
            self.preferences_store.save(preferences)
        except OSError as error:
            messagebox.showerror(self._t("Nie udało się zapisać ustawień", "Could not save preferences"),
                                 str(error), parent=self.root)
            return False
        self.preferences = preferences
        return True

    def _apply_preferences(self, preferences: Preferences) -> bool:
        before = self.preferences
        prefs = self._current_folder_preferences(preferences)
        if not self._write_preferences(prefs):
            return False
        if prefs.theme != before.theme:
            self._set_styles()
            self.table.tag_configure("alternate", background=self._alternate_row_color)
        if prefs.default_conflict != before.default_conflict:
            self.conflict.set(self.conflicts[prefs.default_conflict])
        if prefs.language != self.language:
            status = self._t("Język zmieni się po ponownym uruchomieniu programu.",
                             "The language will change when you restart the app.")
            self.status.set(status)
        return True

    def _profile_selection_changed(self, *_):
        selected = self.custom_profile.get() in self.preferences.profiles and not self.busy
        state = ["!disabled"] if selected else ["disabled"]
        self.load_profile_button.state(state)
        self.remove_profile_button.state(state)

    def _refresh_profile_names(self, name=""):
        self.profile_box.configure(values=sorted(self.preferences.profiles, key=str.casefold))
        self.custom_profile.set(name)

    def _read_plan_filters(self) -> tuple[int, tuple[str, ...]]:
        age = self.min_age.get().strip()
        try:
            days = int(age) if age else 0
        except ValueError:
            days = -1
        if not 0 <= days <= MAX_AGE_DAYS:
            raise ValueError(self._t("Wpisz liczbę pełnych dni od 0 do {limit}.",
                                     "Enter a whole number of days from 0 to {limit}.", limit=MAX_AGE_DAYS))
        names = tuple(dict.fromkeys(part.strip() for part in self.excluded_folders.get().split(";") if part.strip()))
        if any(name in {".", ".."} or any(character in name for character in '/\\:*?"<>|\0') for name in names):
            raise ValueError(self._t("Wpisz same nazwy folderów, np. Kopie;Robocze.",
                                     "Enter folder names, for example Backup;Drafts."))
        return days, names

    def _save_custom_profile(self):
        try:
            age, names = self._read_plan_filters()
            self._parse_extensions()
        except ValueError as error:
            messagebox.showwarning(self._t("Sprawdź ustawienia", "Check settings"), str(error), parent=self.root)
            return
        name = simpledialog.askstring(self._t("Zapisz profil", "Save profile"),
                                      self._t("Podaj nazwę profilu.", "Enter a name for this profile."),
                                      parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        profiles = self.preferences.profiles.copy()
        existing = next((key for key in profiles if key.casefold() == name.casefold()), None)
        if existing is not None:
            replace_profile = messagebox.askyesno(
                self._t("Zastąpić profil?", "Replace this profile?"), existing, parent=self.root, default="no")
            if not replace_profile:
                return
            del profiles[existing]
        profiles[name] = Profile(
            self.source.get().strip(), self.destination.get().strip(), self.extensions.get(),
            self.recursive.get(), self._selected_key(self.groups, self.grouping),
            self._selected_key(self.operations, self.operation), self._selected_key(self.conflicts, self.conflict),
            age, "; ".join(names),
        )
        if self._write_preferences(replace(self.preferences, profiles=profiles)):
            self._refresh_profile_names(name)
            self.status.set(self._t("Zapisano profil „{name}”.", "Saved profile “{name}”.", name=name))

    def _load_custom_profile(self):
        name = self.custom_profile.get()
        profile = self.preferences.profiles.get(name)
        if profile is None:
            return
        if profile.source:
            self._manual_destination = False
            self.source.set(profile.source)
        if profile.destination:
            self.destination.set(profile.destination)
        self.extensions.set(profile.extensions)
        self.recursive.set(profile.recursive)
        self.grouping.set(self.groups[profile.grouping])
        self.operation.set(self.operations[profile.operation])
        self.conflict.set(self.conflicts[profile.conflict])
        self.min_age.set(str(profile.min_age_days))
        self.excluded_folders.set(profile.excluded_folders)
        self.status.set(self._t("Wczytano profil „{name}”. Pokaż podgląd, aby sprawdzić plan.",
                                "Loaded profile “{name}”. Show the preview to check the plan.", name=name))

    def _remove_custom_profile(self):
        name = self.custom_profile.get()
        if name not in self.preferences.profiles:
            return
        confirmed = messagebox.askyesno(self._t("Usunąć zapisany profil?", "Delete this saved profile?"),
                                        name, parent=self.root, default="no")
        if confirmed:
            profiles = self.preferences.profiles.copy()
            del profiles[name]
            if self._write_preferences(replace(self.preferences, profiles=profiles)):
                self._refresh_profile_names()

    def _apply_profile(self, photos: bool):
        if photos:
            self.extensions.set(", ".join(CATEGORIES["Zdjęcia"].split()))
            self.recursive.set(True)
            self.grouping.set(self.groups["original"])
        else:
            if self._read_path(self.source) is None:
                downloads = Path.home() / "Downloads"
                if downloads.is_dir():
                    self.source.set(str(downloads))
            self.extensions.set("*")
            self.recursive.set(False)
            self.grouping.set(self.groups["category"])
        self.operation.set(self.operations["copy"])
        self.min_age.set("0")
        self.excluded_folders.set("")

    def _grouping_changed(self, *_):
        hint = DATE_GROUPING_HELP.get(self._selected_key(self.groups, self.grouping))
        if hint:
            self.grouping_help.configure(text=self._t(*hint))
            self.grouping_help.grid()
        else:
            self.grouping_help.grid_remove()
        self._invalidate()

    def _scope_changed(self, *_):
        self.report.reset()
        self._invalidate()

    def _clear_table(self):
        rows = self.table.get_children()
        if rows:
            self.table.delete(*rows)
        self._details.clear()

    def _invalidate(self, *_):
        self.plan = []
        self._preview_issues = []
        self._failed_transfers = ()
        self._excluded.clear()
        self._page = 0
        self._paths = None
        self._last_destination = None
        self.preview_title.configure(text=self._t("Podgląd", "Preview"))
        self._clear_table()
        if self.source.get().strip():
            self.summary.set(self._t("Kliknij „Pokaż podgląd”, aby sprawdzić plan porządkowania.",
                                     "Click “Show preview” to check the plan."))
        else:
            self.summary.set(self._t("Wybierz folder źródłowy i kliknij „Pokaż podgląd”.",
                                     "Choose a source folder and click “Show preview”."))
        self.status.set("")
        self.progress.configure(value=0)
        self._update_controls()

    def _update_controls(self):
        for widget in self._controls:
            widget.state(["disabled"] if self.busy else ["!disabled"])
        self._profile_selection_changed()
        self.report.set_busy(self.busy, cancelling=self._stop.is_set())
        self.history_panel.set_busy(bool(self.busy))
        active_tab = {"preview": 0, "transfer": 0, "analysis": 1, "recycle": 1, "undo": 2}.get(self.busy)
        for index in range(3):
            self.tabs.tab(index, state="normal" if active_tab is None or index == active_tab else "disabled")
        self.open_button.state(["!disabled"] if self._last_destination and not self.busy else ["disabled"])
        if self.busy == "preview":
            self.preview_button.configure(text=self._t("Zatrzymaj podgląd", "Stop preview"), command=self._cancel)
            self.preview_button.state(["disabled"] if self._stop.is_set() else ["!disabled"])
        else:
            self.preview_button.configure(text=self._t("Pokaż podgląd", "Show preview"), command=self.preview)
            self.preview_button.state(["disabled"] if self.busy else ["!disabled"])
        self.retry_button.state(["!disabled"] if self._failed_transfers and not self.busy else ["disabled"])
        self.preview_issues_button.state(["!disabled"] if self._preview_issues and not self.busy else ["disabled"])
        self._update_selection_controls()
        if self.busy == "transfer":
            self.execute_button.configure(text=self._t("Zatrzymaj", "Stop"), command=self._cancel)
            self.execute_button.state(["disabled"] if self._stop.is_set() else ["!disabled"])
        else:
            moving = self.operation.get() == self.operations["move"]
            label = self._t("Przenieś pliki", "Move files") if moving else self._t("Kopiuj pliki", "Copy files")
            self.execute_button.configure(text=label, command=self.execute)
            can_execute = len(self.plan) > len(self._excluded) and not self.busy
            self.execute_button.state(["!disabled"] if can_execute else ["disabled"])

    def _update_selection_controls(self):
        available = bool(self.plan) and not self.busy
        states = (
            (self.select_all, available and bool(self._excluded)),
            (self.select_none, available and len(self.plan) > len(self._excluded)),
            (self.prev_page, available and self._page > 0),
            (self.next_page, available and (self._page + 1) * PREVIEW_PAGE_SIZE < len(self.plan)),
        )
        for button, enabled in states:
            button.state(["!disabled"] if enabled else ["disabled"])
        total = (len(self.plan) + PREVIEW_PAGE_SIZE - 1) // PREVIEW_PAGE_SIZE
        self.page_label.set(f"{self._page + 1} / {total}" if total > 1 else "")

    def _read_scope(self):
        source = self._read_path(self.source)
        if source is None or not source.is_absolute():
            raise ValueError(self._t("Wybierz folder źródłowy lub wpisz jego pełną ścieżkę.",
                                     "Choose a source folder or enter its full path."))
        return source, self._parse_extensions(), self.recursive.get()

    def _parse_extensions(self):
        try:
            return parse_extensions(self.extensions.get())
        except ValueError as error:
            raise ValueError(self._t(str(error), "Enter extensions, for example jpg, pdf, or * for all files.")) from error

    def preview(self):
        if self.busy:
            return
        self._invalidate()
        try:
            source, extensions, recursive = self._read_scope()
            destination = self._read_path(self.destination)
            if destination is None or not destination.is_absolute():
                raise ValueError(self._t("Wybierz folder docelowy lub wpisz jego pełną ścieżkę.",
                                         "Choose a destination folder or enter its full path."))
            min_age_days, excluded_folders = self._read_plan_filters()
        except ValueError as error:
            messagebox.showwarning(self._t("Sprawdź ustawienia", "Check settings"), str(error), parent=self.root)
            return

        self._stop.clear()
        self.busy = "preview"
        self.tabs.select(0)
        self._update_controls()
        self.summary.set(self._t("Wyszukiwanie pasujących plików…", "Finding matching files…"))
        self.progress.configure(mode="indeterminate")
        self.progress.start(14)
        arguments = (source, destination, extensions, recursive, self._selected_key(self.groups, self.grouping),
                     self.conflict.get() == self.conflicts["skip"], min_age_days, excluded_folders)
        self._start_worker(self._scan, *arguments)

    def analyze(self, duplicates):
        if self.busy:
            return
        try:
            source, extensions, recursive = self._read_scope()
        except ValueError as error:
            messagebox.showwarning(self._t("Sprawdź ustawienia", "Check settings"), str(error), parent=self.root)
            return
        destination = self._read_path(self.destination)
        if destination is not None and not destination.is_absolute():
            destination = None
        self._stop.clear()
        self.busy = "analysis"
        self.tabs.select(self.report)
        self._update_controls()
        self.report.start(duplicates)
        self._start_worker(self._analyze, source, extensions, recursive, destination, duplicates)

    def _analyze(self, source, extensions, recursive, destination, duplicates):
        source = source.resolve()
        try:
            report = analyze_files(source, extensions, recursive, exclude=destination, find_duplicates=duplicates,
                                   cancelled=self._stop.is_set)
        except AnalysisCancelled:
            self._events.put(("analysis_cancelled", None))
        else:
            self._events.put(("analysis", (source, report, duplicates)))

    def recycle_duplicates(self, keeper, copies):
        if self.busy or not copies:
            return
        source = self._read_path(self.source)
        if source is None or not messagebox.askyesno(
            self._t("Potwierdź przeniesienie do Kosza", "Confirm moving to the Recycle Bin"),
            self._t("Przenieść zaznaczone kopie do Kosza ({count})?\n\nZachowasz plik:\n{path}",
                    "Move the selected copies to the Recycle Bin ({count})?\n\nThis file will be kept:\n{path}",
                    count=len(copies), path=keeper.path),
            parent=self.root, default="no",
        ):
            return
        self.busy = "recycle"
        self.tabs.select(self.report)
        self._update_controls()
        self.report.summary.set(self._t("Sprawdzanie kopii przed przeniesieniem do Kosza…",
                                        "Checking copies before sending them to the Recycle Bin…"))
        self.report.progress.start(15)
        self._start_worker(self._recycle, keeper, copies, source)

    def _recycle(self, keeper, copies, source):
        result = recycle_duplicate_files(keeper, copies, source=source.resolve(strict=True))
        self._events.put(("recycled", result))

    def _history_tab_selected(self, _event):
        if self.tabs.select() == str(self.history_panel) and not self.busy:
            self._refresh_history()

    def _refresh_history(self):
        try:
            self.history_panel.show_batches(self.history.batches())
        except OSError as error:
            self.history_panel.show_batches([])
            self.history_panel.summary.set(str(error))

    def undo_history(self, batch_id):
        if self.busy:
            return
        batch = self.history_panel.batches.get(str(batch_id))
        if batch is None:
            try:
                batch = next((entry for entry in self.history.batches() if entry.id == batch_id), None)
            except OSError as error:
                messagebox.showerror(self._t("Nie udało się odczytać historii", "Could not read history"),
                                     str(error), parent=self.root)
                return
        if batch is None or not (batch.remaining or batch.uncertain):
            return
        if batch.remaining and not messagebox.askyesno(
            self._t("Cofnij przeniesienie", "Undo move"),
            self._t("Spróbować przywrócić pliki do wcześniejszych lokalizacji ({count})?",
                    "Try restoring the files to their previous locations ({count})?", count=batch.remaining),
            parent=self.root, default="no",
        ):
            return
        self.busy = "undo"
        self.tabs.select(self.history_panel)
        self._update_controls()
        self.history_panel.summary.set(self._t("Sprawdzanie i przywracanie plików…", "Checking and restoring files…"))
        self._start_worker(self._undo, batch_id)

    def _undo(self, batch_id):
        self._events.put(("undone", self.history.undo(batch_id)))

    def _start_worker(self, work, *arguments):
        def run():
            try:
                work(*arguments)
            except Exception as error:
                self._events.put(("error", error))

        Thread(target=run, daemon=True).start()
        self.root.after(50, self._poll_worker)

    def _scan(self, source, destination, extensions, recursive, grouping, skip_existing, min_age_days, excluded_folders):
        source = source.resolve()
        destination = destination.resolve()
        issues = []

        def checkpoint():
            if self._stop.is_set():
                raise AnalysisCancelled

        try:
            plan = build_plan(source, destination, extensions, recursive, grouping == "extension",
                              group_by_category=grouping == "category", group_by_date=grouping == "modified",
                              group_by_size=grouping == "size", skip_existing=skip_existing,
                              group_by_original_date=grouping == "original",
                              group_by_creation_date=grouping == "created", min_age_days=min_age_days,
                              excluded_folders=excluded_folders, issues=issues, checkpoint=checkpoint)
            checkpoint()
        except AnalysisCancelled:
            self._events.put(("preview_cancelled", None))
        else:
            protected = self.preferences_store.path.resolve()
            plan = [item for item in plan if protected not in (item.source, item.destination)]
            self._events.put(("preview", (source, destination, plan, issues)))

    def _poll_worker(self):
        while True:
            try:
                event, payload = self._events.get_nowait()
            except Empty:
                if self.busy:
                    self.root.after(50, self._poll_worker)
                return
            if event == "preview":
                self._show_plan(*payload)
            elif event == "preview_cancelled":
                self.busy = ""
                self.progress.stop()
                self.progress.configure(mode="determinate", value=0)
                self._invalidate()
                self.status.set(self._t("Podgląd zatrzymany.", "Preview stopped."))
            elif event == "analysis":
                self.busy = ""
                self.report.show(*payload)
                self._update_controls()
            elif event == "analysis_cancelled":
                self.busy = ""
                self.report.stop()
                self.report.reset()
                self.report.summary.set(self._t("Analiza została zatrzymana.", "Analysis stopped."))
                self._update_controls()
            elif event == "progress":
                current, total = payload
                self.progress.configure(value=current)
                if not self._stop.is_set():
                    self.status.set(self._t("Przetworzono {current} z {total}", "Processed {current} of {total}",
                                            current=current, total=total))
            elif event == "finished":
                self._show_result(*payload)
            elif event == "preflight_failed":
                self.busy = ""
                self.status.set(describe_issue(payload, self.language))
                self._update_controls()
            elif event == "recycled":
                self.busy = ""
                if payload.recycled:
                    self._invalidate()
                self.report.show_cleanup(payload)
                self._update_controls()
            elif event == "undone":
                self.busy = ""
                if payload.restored:
                    self._scope_changed()
                self._refresh_history()
                self.history_panel.show_result(payload)
                self._update_controls()
            elif event == "error":
                previous_operation = self.busy
                self.busy = ""
                if previous_operation in {"analysis", "recycle"}:
                    self.report.stop()
                    self.report.reset()
                    self._update_controls()
                elif previous_operation == "undo":
                    self._refresh_history()
                    self._update_controls()
                else:
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=0)
                    self._invalidate()
                messagebox.showerror(self._t("Operacja przerwana", "Operation interrupted"),
                                     describe_issue(payload, self.language), parent=self.root)

    def _display_row(self, transfer: Transfer, note: str, *, is_preview=False):
        source, destination = self._paths
        original = os.path.relpath(transfer.source, source)
        target = os.path.relpath(transfer.destination, destination)
        tags = ("alternate",) if len(self._details) % 2 else ()
        checkbox = self._checkbox(transfer) if is_preview else ""
        item = self.table.insert("", "end", values=(checkbox, original, target, note), tags=tags)
        self._details[item] = (transfer, note)

    def _checkbox(self, transfer):
        return "☐" if transfer in self._excluded else "☑"

    def _render_page(self):
        self._clear_table()
        start = self._page * PREVIEW_PAGE_SIZE
        for transfer in self.plan[start:start + PREVIEW_PAGE_SIZE]:
            note = self._t("Zmiana nazwy", "Renamed") if transfer.source.name != transfer.destination.name else ""
            self._display_row(transfer, note, is_preview=True)
        self.table.yview_moveto(0)

    def _refresh_selection(self):
        selected = len(self.plan) - len(self._excluded)
        self.summary.set(self._t("Wybrane pliki ({selected} z {total})  ·  Zmiany nazw w planie ({renamed})",
                                 "Selected files ({selected} of {total})  ·  Planned renames ({renamed})",
                                 selected=selected, total=len(self.plan), renamed=self._renamed))
        self._update_controls()

    def _change_page(self, offset):
        target = self._page + offset
        if not self.busy and 0 <= target and target * PREVIEW_PAGE_SIZE < len(self.plan):
            self._page = target
            self._render_page()
            self._update_selection_controls()

    def _set_selection(self, selected):
        if not self.busy and self.plan:
            self._excluded = set() if selected else set(self.plan)
            for row, (transfer, _) in self._details.items():
                self.table.set(row, "include", self._checkbox(transfer))
            self._refresh_selection()

    def _toggle_row(self, row):
        if self.busy or not self.plan or row not in self._details:
            return
        transfer, _ = self._details[row]
        if transfer in self._excluded:
            self._excluded.remove(transfer)
        else:
            self._excluded.add(transfer)
        self.table.set(row, "include", self._checkbox(transfer))
        self._refresh_selection()

    def _toggle_click(self, event):
        if self.plan and self.table.identify_region(event.x, event.y) == "cell":
            if self.table.identify_column(event.x) == "#1":
                row = self.table.identify_row(event.y)
                if row:
                    self.table.focus_set()
                    self.table.focus(row)
                    self.table.selection_set(row)
                    self._toggle_row(row)
                return "break"

    def _toggle_focused(self, _event):
        self._toggle_row(self.table.focus())
        return "break"

    def _show_plan(self, source, destination, plan, issues):
        self._paths = (source, destination)
        self.plan = plan
        self._preview_issues = issues
        self.busy = ""
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        if not plan:
            message = self._t("Nie znaleziono pasujących plików.", "No matching files found.")
            if self.conflict.get() == self.conflicts["skip"]:
                message = self._t("Brak pasujących plików lub wszystkie nazwy są zajęte w folderze docelowym.",
                                  "No files match or all names are in use in the destination.")
            self.summary.set(message)
        else:
            self._renamed = sum(item.source.name != item.destination.name for item in plan)
            self._render_page()
            self._refresh_selection()
            self.status.set(self._t("Kliknij pole wyboru lub użyj spacji.", "Click a checkbox or press Space."))
        if issues:
            self.status.set(self._t("Problemy z odczytem lub dostępem ({count}).",
                                    "Files or folders unavailable ({count}).", count=len(issues)))
        self._update_controls()

    def execute(self):
        if self.busy:
            return
        selected = tuple(item for item in self.plan if item not in self._excluded)
        self._start_transfers(selected, self.operation.get() == self.operations["move"])

    def _start_transfers(self, selected, moving):
        if self.busy or not selected or self._paths is None:
            return
        destination = self._paths[1]
        if moving:
            confirm = messagebox.askyesno(
                self._t("Potwierdź przenoszenie", "Confirm move"),
                self._t("Przenieść wybrane pliki ({count}) do folderu\n{path}?\n\n"
                        "Po udanym przeniesieniu pliki znikną z folderu źródłowego.",
                        "Move the selected files ({count}) to\n{path}?\n\n"
                        "The files that move successfully will no longer be in the source folder.",
                        count=len(selected), path=destination),
                parent=self.root, default="no",
            )
            if not confirm:
                return

        self.busy = "transfer"
        self._stop.clear()
        self._update_controls()
        self.status.set(self._t("Sprawdzanie folderu docelowego…", "Checking the destination folder…"))
        self.progress.configure(mode="determinate", maximum=len(selected), value=0)
        self._start_worker(self._run_transfers, selected, moving, destination)

    def _run_transfers(self, plan, moving, destination):
        try:
            check_destination(destination, plan, moving=moving)
            batch_id = self.history.start_batch(moving) if not self._stop.is_set() else None
        except OSError as error:
            self._events.put(("preflight_failed", error))
            return
        successful = 0
        failures = []
        retryable = []
        last_update = monotonic()
        for transfer in plan:
            if self._stop.is_set():
                break
            try:
                self.history.transfer(batch_id, transfer, moving)
                successful += 1
            except (OSError, ValueError) as error:
                failures.append((transfer, error))
                if isinstance(error, HistoryError):
                    break
                retryable.append(transfer)
            now = monotonic()
            if now - last_update >= 0.08:
                self._events.put(("progress", (successful + len(failures), len(plan))))
                last_update = now
        self._events.put(("finished", (moving, successful, len(plan), failures, tuple(retryable))))

    def _cancel(self):
        self._stop.set()
        if self.busy == "analysis":
            self.report.hint.set(self._t("Zatrzymywanie analizy…", "Stopping analysis…"))
        elif self.busy == "preview":
            self.status.set(self._t("Zatrzymywanie podglądu…", "Stopping preview…"))
        else:
            self.status.set(self._t("Zatrzymywanie po bieżącym pliku…", "Stopping after the current file…"))
        self._update_controls()

    def _show_result(self, moving, successful, total, failures, retryable):
        failed = len(failures)
        self._failed_transfers = retryable
        self._retry_move = moving
        self.busy = ""
        self.plan = []
        self._excluded.clear()
        self._page = 0
        if successful:
            self._last_destination = self._paths[1]
        if successful:
            self.report.reset()
        self._clear_table()
        self.preview_title.configure(text=self._t("Wynik", "Result"))
        self.progress.configure(value=successful + failed)
        completed = self._t("Przeniesiono", "Moved") if moving else self._t("Skopiowano", "Copied")
        self.summary.set(self._t("{action} ({success})  ·  Błędy ({failed})  ·  Bez próby ({skipped})",
                                 "{action} ({success})  ·  Errors ({failed})  ·  Not attempted ({skipped})",
                                 action=completed, success=successful, failed=failed, skipped=total - successful - failed))
        for transfer, error in failures[:PREVIEW_LIMIT]:
            detail = describe_issue(error, self.language)
            if self.language == "en" and isinstance(error, HistoryError):
                detail = f"The result could not be verified. Check both folders and the History tab.\n\n{detail}"
            self._display_row(transfer, detail)
        if failed > PREVIEW_LIMIT:
            self.status.set(self._t("Pierwsze {limit} błędów. Kliknij plik dwukrotnie.",
                                    "Showing the first {limit} errors. Double-click a file.", limit=PREVIEW_LIMIT))
        elif failed:
            self.status.set(self._t("Kliknij plik dwukrotnie, aby zobaczyć błąd.",
                                    "Double-click a file to see the error."))
        else:
            text = self._t("Gotowe.", "Done.") if successful == total else self._t("Operacja zatrzymana.", "Stopped.")
            self.status.set(text)
        self._update_controls()

    def retry_failed(self):
        self._start_transfers(self._failed_transfers, self._retry_move)

    def _show_details(self, event):
        if self.plan and self.table.identify_column(event.x) == "#1":
            return "break"
        item = self.table.identify_row(event.y)
        if item not in self._details:
            return
        transfer, note = self._details[item]
        messagebox.showinfo(
            self._t("Szczegóły pliku", "File details"),
            self._t("Źródło\n{source}\n\nCel\n{destination}\n\n{note}",
                    "Source\n{source}\n\nDestination\n{destination}\n\n{note}",
                    source=transfer.source, destination=transfer.destination, note=note),
            parent=self.root,
        )

    def _open_destination(self):
        if self._last_destination is not None:
            try:
                os.startfile(self._last_destination)
            except OSError as error:
                messagebox.showerror(self._t("Nie udało się otworzyć folderu", "Could not open the folder"),
                                     str(error), parent=self.root)

    def _close(self):
        if self.busy in {"undo", "recycle"}:
            messagebox.showinfo(self._t("Operacja w toku", "Operation in progress"),
                                self._t("Poczekaj na zakończenie operacji przed zamknięciem programu.",
                                        "Wait for the operation to finish before closing the app."), parent=self.root)
        elif self.busy == "transfer":
            messagebox.showinfo(self._t("Trwa porządkowanie", "Organizing files"),
                                self._t("Kliknij „Zatrzymaj” i poczekaj na zakończenie bieżącego pliku.",
                                        "Click “Stop” and wait for the current file to finish."), parent=self.root)
        else:
            if self.preferences.remember_folders:
                prefs = self._current_folder_preferences(self.preferences)
                if prefs != self.preferences:
                    self._write_preferences(prefs)
            self.root.destroy()


def main():
    root = tk.Tk()
    OrganizerWindow(root)
    root.mainloop()
