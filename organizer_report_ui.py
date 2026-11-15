import os
from functools import partial
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from file_analysis import AnalysisReport
from organizer import modification_date


ROW_LIMIT = 500
ENGLISH_CATEGORIES = {
    "Zdjęcia": "Photos",
    "Dokumenty": "Documents",
    "Wideo": "Videos",
    "Audio": "Audio",
    "Archiwa": "Archives",
    "Instalatory": "Installers",
    "Inne": "Other",
}


def _translate(language: str, polish: str, english: str) -> str:
    return english if language == "en" else polish


def format_size(size: int, language: str = "pl") -> str:
    for unit, threshold in (("TiB", 1024**4), ("GiB", 1024**3), ("MiB", 1024**2), ("KiB", 1024)):
        if size >= threshold:
            value = f"{size / threshold:.1f} {unit}"
            return value if language == "en" else value.replace(".", ",")
    return f"{size} B"


class ReportPanel(ttk.Frame):
    def __init__(self, parent, analyze, cancel, *, recycle=None, language="pl"):
        super().__init__(parent, style="Panel.TFrame", padding=(14, 10))
        self.language = language
        self._text = text = partial(_translate, language)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.summary = tk.StringVar(self)
        self.hint = tk.StringVar(self)
        self._locations = {}
        self._duplicate_groups = {}
        self._busy = ""
        self._recycle = recycle
        self._source = None

        ttk.Label(self, textvariable=self.summary, style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(self, textvariable=self.hint, style="Help.TLabel", wraplength=650).grid(
            row=1, column=0, sticky="w", pady=(3, 8))
        self.views = ttk.Notebook(self)
        self.views.grid(row=2, column=0, sticky="nsew")
        category = text("Kategoria", "Category")
        size = text("Rozmiar", "Size")
        self.categories = self._make_table(text("Kategorie", "Categories"), (
            ("name", category, 285), ("count", text("Pliki", "Files"), 115), ("size", size, 125),
        ))
        self.largest = self._make_table(text("Największe pliki", "Largest files"), (
            ("path", text("Plik", "File"), 315), ("size", size, 115),
            ("category", category, 115), ("modified", text("Zmodyfikowano", "Modified"), 120),
        ))
        self.duplicates = self._make_table(text("Duplikaty", "Duplicates"), (
            ("size", text("Dodatkowe kopie zajmują", "Size of extra copies"), 190),
        ), tree_heading=text("Grupa lub plik", "Group or file"))
        self.issues = self._make_table(text("Problemy", "Issues"), (
            ("path", text("Ścieżka", "Path"), 300), ("message", text("Komunikat", "Message"), 360),
        ))
        for tree in (self.largest, self.duplicates, self.issues):
            tree.bind("<<TreeviewSelect>>", self._update_open_button)
            tree.bind("<Double-1>", self._show_details)
        self.views.bind("<<NotebookTabChanged>>", self._update_open_button)

        actions = ttk.Frame(self, style="Panel.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        actions.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(actions)
        self.progress.grid(row=0, column=0, columnspan=5, sticky="ew", pady=(0, 7))
        self.open_button = ttk.Button(actions, text=text("Otwórz lokalizację", "Open location"),
                                      command=self._open_selected)
        self.open_button.grid(row=1, column=0, sticky="w")
        self.cleanup_button = ttk.Button(actions, text=text("Uporządkuj grupę…", "Clean up group…"),
                                         command=self._choose_cleanup)
        self.cleanup_button.grid(row=1, column=1, padx=(6, 0))
        if recycle is None:
            self.cleanup_button.grid_remove()
        self.cancel_button = ttk.Button(actions, text=text("Zatrzymaj", "Stop"), command=cancel)
        self.cancel_button.grid(row=1, column=2, padx=(6, 0))
        self.analyze_button = ttk.Button(actions, text=text("Analizuj folder", "Analyze folder"),
                                         command=lambda: analyze(False))
        self.analyze_button.grid(row=1, column=3, padx=(6, 0))
        self.duplicate_button = ttk.Button(actions, text=text("Szukaj duplikatów", "Find duplicates"), style="Accent.TButton",
                                           command=lambda: analyze(True))
        self.duplicate_button.grid(row=1, column=4, padx=(6, 0))
        self.reset()
        self.set_busy("")

    def _make_table(self, title, columns, *, tree_heading=None):
        page = ttk.Frame(self.views)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        self.views.add(page, text=title)
        tree = ttk.Treeview(page, columns=[key for key, _, _ in columns],
                            show="tree headings" if tree_heading else "headings", height=4, selectmode="browse")
        if tree_heading:
            tree.heading("#0", text=tree_heading, anchor="w")
            tree.column("#0", width=480, minwidth=130)
        for index, (key, heading, width) in enumerate(columns):
            tree.heading(key, text=heading, anchor="w")
            tree.column(key, width=width, minwidth=75, stretch=index == 0 and not tree_heading)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(page, orient="vertical", command=tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(page, orient="horizontal", command=tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        return tree

    def reset(self):
        self.summary.set(self._text("Sprawdź zawartość folderu", "Explore the folder"))
        self.hint.set(self._text(
            "Analiza pokazuje kategorie i największe pliki. Wyszukiwanie duplikatów porównuje zawartość plików.",
            "Folder analysis shows file categories and the largest files. Duplicate search compares file contents.",
        ))
        for tree in (self.categories, self.largest, self.duplicates, self.issues):
            tree.delete(*tree.get_children())
        self._locations.clear()
        self._duplicate_groups.clear()
        self._source = None
        self._update_open_button()

    def set_busy(self, busy, cancelling=False):
        self._busy = busy
        for button in (self.analyze_button, self.duplicate_button):
            button.state(["disabled"] if busy else ["!disabled"])
        self.cancel_button.state(["!disabled"] if busy == "analysis" and not cancelling else ["disabled"])
        self._update_open_button()

    def start(self, duplicates):
        self.reset()
        text = self._text
        self.summary.set(text("Porównywanie plików…", "Comparing files…") if duplicates
                         else text("Analizowanie folderu…", "Analyzing folder…"))
        self.hint.set(text("Czas zależy od liczby plików i szybkości dysku.",
                           "The time needed depends on the number of files and the speed of the drive."))
        self.progress.start(15)

    def stop(self):
        self.progress.stop()
        self.progress.configure(value=0)

    def show(self, source: Path, report: AnalysisReport, checked_duplicates: bool):
        self.stop()
        self._source = source
        text = self._text
        size = partial(format_size, language=self.language)
        self.summary.set(text("Pliki ({count})  ·  Łączny rozmiar ({size})", "Files ({count})  ·  Total size ({size})")
                         .format(count=len(report.files), size=size(report.total_bytes)))
        notes = []
        if checked_duplicates:
            extra = sum(group.redundant_bytes for group in report.duplicates)
            notes.append(text("Grupy duplikatów ({count})  ·  Dodatkowe kopie ({size})",
                              "Duplicate groups ({count})  ·  Extra copies ({size})")
                         .format(count=len(report.duplicates), size=size(extra)))
            if self._recycle is not None and report.duplicates:
                notes.append(text("Zaznacz grupę i kliknij „Uporządkuj grupę…”, aby wskazać kopie do Kosza.",
                                  "Select a group and click “Clean up group…” to choose which copies to recycle."))
        else:
            notes.append(text("Kliknij „Szukaj duplikatów”, aby porównać zawartość plików.",
                              "Click “Find duplicates” to compare file contents."))
        if report.issues:
            notes.append(text("Problemy ({count}) – szczegóły w zakładce „Problemy”.",
                              "Issues ({count}) – see the Issues tab for details.").format(count=len(report.issues)))

        category_labels = ENGLISH_CATEGORIES if self.language == "en" else {}
        for category in report.categories[:ROW_LIMIT]:
            label = category_labels.get(category.name, category.name)
            self.categories.insert("", "end", values=(label, category.count, size(category.total_bytes)))
        for file in report.files[:ROW_LIMIT]:
            date = modification_date(file.modified).strftime(text("%d.%m.%Y", "%Y-%m-%d"))
            label = category_labels.get(file.category, file.category)
            values = (file.path.relative_to(source), size(file.size), label, date)
            row = self.largest.insert("", "end", values=values)
            self._locations[(self.largest, row)] = (file.path, "")
        duplicate_rows = 0
        for group_number, group in enumerate(report.duplicates, 1):
            if duplicate_rows >= ROW_LIMIT:
                break
            label = text("Grupa {number} ({count} plików)", "Group {number} ({count} files)").format(
                number=group_number, count=len(group.files))
            row = self.duplicates.insert("", "end", text=label, values=(size(group.redundant_bytes),),
                                         open=group_number <= 3)
            self._duplicate_groups[row] = group.files
            visible = group.files[:ROW_LIMIT - duplicate_rows]
            for file in visible:
                entry = self.duplicates.insert(row, "end", text=file.path.relative_to(source))
                self._locations[(self.duplicates, entry)] = (file.path, "")
            duplicate_rows += len(visible)
        for issue in report.issues[:ROW_LIMIT]:
            self._add_issue(issue.path, issue.message)
        if (max(len(report.files), len(report.issues), len(report.categories)) > ROW_LIMIT
                or sum(len(group.files) for group in report.duplicates) > ROW_LIMIT):
            notes.append(text("Limit widocznych pozycji w zakładce ({limit}).",
                              "Each tab shows up to {limit} items.").format(limit=ROW_LIMIT))
        self.hint.set("\n".join(notes))
        self.views.select(self.duplicates.master if checked_duplicates else self.categories.master)
        self._update_open_button()

    def show_cleanup(self, result):
        self.stop()
        self.reset()
        text = self._text
        self.summary.set(text("Do Kosza ({recycled})  ·  Nie udało się ({failed})",
                              "Moved to Recycle Bin ({recycled})  ·  Failed ({failed})")
                         .format(recycled=len(result.recycled), failed=len(result.failures)))
        self.hint.set(text("Uruchom wyszukiwanie duplikatów ponownie, aby odświeżyć wyniki.",
                           "Search for duplicates again to refresh the results."))
        if result.failures:
            for path, message in result.failures:
                self._add_issue(path, message)
            self.views.select(self.issues.master)

    def _add_issue(self, path, message):
        details = self._text(message, f"Details: {message}")
        row = self.issues.insert("", "end", values=(path, details))
        self._locations[(self.issues, row)] = (path, details)

    def _selected_location(self):
        active = self.views.select()
        for tree in (self.largest, self.duplicates, self.issues):
            if str(tree.master) == active and tree.selection():
                return self._locations.get((tree, tree.selection()[0]))

    def _update_open_button(self, *_):
        self.open_button.state(["!disabled"] if self._selected_location() else ["disabled"])
        enabled = not self._busy and self._recycle is not None and self._selected_group()
        self.cleanup_button.state(["!disabled"] if enabled else ["disabled"])

    def _selected_group(self):
        selection = self.duplicates.selection()
        if selection and self.views.select() == str(self.duplicates.master):
            row = selection[0]
            return self._duplicate_groups.get(self.duplicates.parent(row) or row)

    def _choose_cleanup(self):
        group = self._selected_group()
        if group and self._source is not None and self._recycle is not None and not self._busy:
            CleanupDialog(self, self._source, group, self._recycle, language=self.language)

    def _open_selected(self):
        selection = self._selected_location()
        if selection:
            path, _ = selection
            try:
                os.startfile(path if path.is_dir() else path.parent)
            except OSError as error:
                messagebox.showerror(self._text("Nie udało się otworzyć folderu", "Could not open folder"),
                                     self._text(str(error), f"Details: {error}"), parent=self)

    def _show_details(self, event):
        row = event.widget.identify_row(event.y)
        location = self._locations.get((event.widget, row))
        if location:
            path, note = location
            messagebox.showinfo(self._text("Szczegóły", "Details"), f"{path}\n\n{note}" if note else str(path),
                                parent=self)


class CleanupDialog(tk.Toplevel):
    def __init__(self, parent, source, files, apply, *, language="pl"):
        super().__init__(parent)
        self._text = text = partial(_translate, language)
        self.title(text("Porządkowanie grupy duplikatów", "Clean up duplicate group"))
        background = ttk.Style(self).lookup("Panel.TFrame", "background")
        if background:
            self.configure(background=background)
        self.transient(parent.winfo_toplevel())
        self.minsize(560, 360)
        self.geometry("760x420")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._files = {}
        self._keeper = None
        self._checked = set()
        self._apply = apply

        instructions = text(
            "Zaznacz plik, który chcesz zachować, i kliknij „Zachowaj”.\n"
            "Potem zaznacz wybrane kopie i kliknij „Zaznacz do Kosza”. Aby wybrać kilka pozycji, użyj Ctrl.",
            "Select the file you want to keep and click “Keep”.\n"
            "Then select the copies and click “Mark for Recycle Bin”. Hold Ctrl to select multiple files.",
        )
        ttk.Label(self, text=instructions, style="Panel.TLabel",
                  wraplength=520).grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 8))
        list_frame = ttk.Frame(self, style="Panel.TFrame")
        list_frame.grid(row=1, column=0, sticky="nsew", padx=16)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(list_frame, columns=("decision", "file"), show="headings",
                                  selectmode="extended", height=9)
        self.table.heading("decision", text=text("Decyzja", "Action"), anchor="w")
        self.table.column("decision", width=110, minwidth=110, stretch=False)
        self.table.heading("file", text=text("Plik", "File"), anchor="w")
        self.table.column("file", width=600, minwidth=300)
        self.table.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(list_frame, orient="vertical", command=self.table.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(list_frame, orient="horizontal", command=self.table.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.table.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        self.table.bind("<<TreeviewSelect>>", self._update_actions)
        for file in files:
            row = self.table.insert("", "end", values=("—", file.path.relative_to(source)))
            self._files[row] = file

        controls = ttk.Frame(self, style="Panel.TFrame")
        controls.grid(row=2, column=0, sticky="ew", padx=16, pady=8)
        self.keep_button = ttk.Button(controls, text=text("Zachowaj", "Keep"), command=self._keep_selected)
        self.keep_button.pack(side="left")
        self.check_button = ttk.Button(controls, text=text("Zaznacz do Kosza", "Mark for Recycle Bin"),
                                      command=self._check_selected)
        self.check_button.pack(side="left", padx=(6, 0))
        self.uncheck_button = ttk.Button(controls, text=text("Odznacz", "Unmark"), command=self._uncheck_selected)
        self.uncheck_button.pack(side="left", padx=(6, 0))
        footer = ttk.Frame(self, style="Panel.TFrame")
        footer.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 12))
        self.status = tk.StringVar(self)
        ttk.Label(footer, textvariable=self.status, style="Panel.TLabel").pack(side="left")
        self.apply_button = ttk.Button(footer, text=text("Przenieś do Kosza", "Move to Recycle Bin"),
                                       command=self._confirm)
        self.apply_button.pack(side="right")
        ttk.Button(footer, text=text("Anuluj", "Cancel"), command=self.destroy).pack(side="right", padx=(0, 6))
        self.bind("<Escape>", lambda event: self.destroy())
        self._update_actions()
        self.update_idletasks()
        self.grab_set()
        self.table.focus_set()

    def _update_actions(self, *_):
        selection = set(self.table.selection())
        can_check = self._keeper is not None and bool(selection - self._checked - {self._keeper})
        for button, enabled in ((self.keep_button, len(selection) == 1),
                                (self.check_button, can_check),
                                (self.uncheck_button, bool(selection & self._checked)),
                                (self.apply_button, self._keeper is not None and bool(self._checked))):
            button.state(["!disabled"] if enabled else ["disabled"])
        self.status.set(self._text("Wybrane kopie ({count})", "Selected copies ({count})")
                        .format(count=len(self._checked)))

    def _keep_selected(self):
        selection = self.table.selection()
        if len(selection) == 1:
            if self._keeper is not None:
                self.table.set(self._keeper, "decision", "—")
            self._keeper = selection[0]
            self._checked.discard(self._keeper)
            self.table.set(self._keeper, "decision", self._text("Zachowaj", "Keep"))
            self._update_actions()

    def _check_selected(self):
        if self._keeper is not None:
            for row in self.table.selection():
                if row != self._keeper:
                    self._checked.add(row)
                    self.table.set(row, "decision", self._text("Do Kosza", "Recycle"))
            self._update_actions()

    def _uncheck_selected(self):
        for row in self.table.selection():
            if row in self._checked:
                self._checked.remove(row)
                self.table.set(row, "decision", "—")
        self._update_actions()

    def _confirm(self):
        if self._keeper is not None and self._checked:
            keeper = self._files[self._keeper]
            copies = tuple(file for row, file in self._files.items() if row in self._checked)
            self.destroy()
            self._apply(keeper, copies)
