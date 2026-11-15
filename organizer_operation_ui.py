import tkinter as tk
from tkinter import ttk

from operation_feedback import describe_issue


def show_preview_issues(parent, issues, language):
    english = language == "en"
    window = tk.Toplevel(parent)
    window.title("Preview issues" if english else "Problemy w podglądzie")
    window.transient(parent)
    window.bind("<Escape>", lambda _event: window.destroy())
    panel = ttk.Frame(window, style="Panel.TFrame", padding=12)
    panel.pack(fill="both", expand=True)
    panel.columnconfigure(0, weight=1)
    panel.rowconfigure(1, weight=1)

    title = (f"Unavailable files or folders: {len(issues)}" if english
             else f"Niedostępne pliki lub foldery: {len(issues)}")
    ttk.Label(panel, text=title, style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
    table = ttk.Treeview(panel, columns=("path", "reason"), show="headings", height=8)
    table.heading("path", text="Path" if english else "Ścieżka", anchor="w")
    table.heading("reason", text="Reason" if english else "Powód", anchor="w")
    table.column("path", width=300, stretch=True)
    table.column("reason", width=390, stretch=True)
    for entry in issues[:500]:
        table.insert("", "end", values=(str(entry.path), describe_issue(entry.error, language)))
    table.grid(row=1, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(panel, command=table.yview)
    scrollbar.grid(row=1, column=1, sticky="ns")
    table.configure(yscrollcommand=scrollbar.set)
    if len(issues) > 500:
        caption = "Showing the first 500 entries." if english else "Pierwsze 500 pozycji."
        ttk.Label(panel, text=caption, style="Help.TLabel").grid(row=2, column=0, sticky="w", pady=(6, 0))

    def inspect(event):
        row = table.identify_row(event.y)
        if row:
            from tkinter import messagebox

            path, reason = table.item(row, "values")
            messagebox.showinfo(window.title(), f"{path}\n\n{reason}", parent=window)

    table.bind("<Double-1>", inspect)
    ttk.Button(panel, text="Close" if english else "Zamknij", command=window.destroy).grid(
        row=3, column=0, sticky="e", pady=(8, 0))
    window.grab_set()
    return window
