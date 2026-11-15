import tkinter as tk
from tkinter import messagebox, ttk


class HistoryPanel(ttk.Frame):
    def __init__(self, parent, undo, *, language="pl"):
        super().__init__(parent, style="Panel.TFrame", padding=(14, 10))
        self.language = language
        text = self._text
        self.undo = undo
        self.batches = {}
        self.busy = False
        self.summary = tk.StringVar(self, text("Tutaj znajdziesz operacje wykonane w Organizatorze.",
                                              "Review operations performed in File Organizer."))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=2)
        self.rowconfigure(3, weight=1)

        ttk.Label(self, textvariable=self.summary, style="Help.TLabel", wraplength=650).grid(
            row=0, column=0, sticky="w", pady=(0, 8))
        self.table = self._make_table(1, (
            ("time", text("Data i godzina", "Date and time"), 195),
            ("operation", text("Operacja", "Operation"), 155),
            ("completed", text("Wykonano", "Completed"), 90),
            ("remaining", text("Do przywrócenia", "Can restore"), 122),
            ("uncertain", text("Do sprawdzenia", "Needs review"), 115),
        ))
        self.table.bind("<<TreeviewSelect>>", lambda _event: self._update_button())
        ttk.Label(self, text=text("Problemy z przywracaniem", "Restore issues"), style="Section.TLabel").grid(
            row=2, column=0, sticky="w", pady=(9, 4))
        self.issues = self._make_table(3, (("path", text("Plik", "File"), 250),
                                           ("message", text("Powód", "Reason"), 420)))
        self.issues.bind("<Double-1>", self._show_issue)
        self.undo_button = ttk.Button(self, style="Accent.TButton", command=self._undo_selected)
        self.undo_button.grid(row=4, column=0, sticky="e", pady=(8, 0))
        self._update_button()

    def _text(self, polish, english):
        return english if self.language == "en" else polish

    def _make_table(self, row, columns):
        frame = ttk.Frame(self, style="Panel.TFrame")
        frame.grid(row=row, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        table = ttk.Treeview(frame, columns=[key for key, _, _ in columns], show="headings",
                             height=3, selectmode="browse")
        for index, (key, title, width) in enumerate(columns):
            table.heading(key, text=title, anchor="w")
            table.column(key, width=width, minwidth=90, stretch=index == 0)
        scroll = ttk.Scrollbar(frame, command=table.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        table.configure(yscrollcommand=scroll.set)
        table.grid(row=0, column=0, sticky="nsew")
        return table

    def show_batches(self, batches):
        text = self._text
        selection = self.table.selection()
        self.table.delete(*self.table.get_children())
        self.batches = {str(batch.id): batch for batch in batches}
        for key, batch in self.batches.items():
            operation = text("Przeniesienie", "Move") if batch.move else text("Kopiowanie", "Copy")
            self.table.insert("", "end", iid=key, values=(
                batch.created_at, operation, batch.completed, batch.remaining if batch.move else "—", batch.uncertain,
            ))
        if selection and selection[0] in self.batches:
            self.table.selection_set(selection[0])
        self.summary.set(text("Wybierz przeniesienie, aby przywrócić pliki do ich dawnych lokalizacji.",
                              "Select a move to restore files to their previous locations.")
                         if batches else text("Nie ma jeszcze zapisanych operacji.", "No operations recorded yet."))
        self._update_button()

    def show_result(self, result):
        self.issues.delete(*self.issues.get_children())
        for issue in result.issues:
            details = self._text(issue.message, f"Details: {issue.message}")
            self.issues.insert("", "end", values=(issue.path, details))
        self.summary.set(self._text("Przywrócono pliki: {restored}  ·  Problemy: {issues}",
                                    "Files restored: {restored}  ·  Issues: {issues}")
                         .format(restored=result.restored, issues=len(result.issues)))

    def set_busy(self, busy):
        self.busy = busy
        self._update_button()

    def _selected_batch(self):
        selection = self.table.selection()
        return self.batches.get(selection[0]) if selection else None

    def _update_button(self):
        batch = self._selected_batch()
        enabled = not self.busy and batch is not None and (batch.remaining > 0 or batch.uncertain > 0)
        label = (self._text("Sprawdź przerwaną operację", "Review interrupted operation")
                 if batch and not batch.remaining and batch.uncertain
                 else self._text("Cofnij wybrane przeniesienie", "Undo selected move"))
        self.undo_button.configure(text=label)
        self.undo_button.state(["!disabled"] if enabled else ["disabled"])

    def _undo_selected(self):
        batch = self._selected_batch()
        if not self.busy and batch is not None and (batch.remaining or batch.uncertain):
            self.undo(batch.id)

    def _show_issue(self, event):
        row = self.issues.identify_row(event.y)
        if row:
            path, message = self.issues.item(row, "values")
            messagebox.showinfo(self._text("Nie przywrócono pliku", "File was not restored"),
                                f"{path}\n\n{message}", parent=self)
