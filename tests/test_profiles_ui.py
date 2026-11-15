from datetime import datetime
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import tkinter as tk
from tkinter import ttk
import unittest

from PIL import ExifTags, Image

from organizer_ui import OrganizerWindow


class ProfileAndPreviewTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Brak środowiska graficznego Tk: {error}")
        self.addCleanup(self.root.destroy)
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        fixture = Path(folder.name).resolve()
        self.app = OrganizerWindow(self.root, history_path=fixture / "log" / "history.sqlite3")
        self.source = fixture / "source"
        self.source.mkdir()
        self.destination = self.source / "Uporządkowane"
        self.app.source.set(str(self.source))
        self.event_time = 0
        self.root.update()

    def await_ready(self):
        deadline = monotonic() + 5
        while self.app.busy and monotonic() < deadline:
            self.root.update()
            sleep(0.01)
        self.assertFalse(self.app.busy, "Operacja nie zakończyła się w ciągu 5 s.")
        self.root.update()

    def click(self, label):
        pending = [self.root]
        while pending:
            widget = pending.pop()
            if isinstance(widget, ttk.Button) and widget.cget("text") == label:
                self.assertFalse(widget.instate(["disabled"]))
                widget.invoke()
                self.root.update()
                return
            pending.extend(widget.winfo_children())
        self.fail(f"Brak przycisku {label!r}")

    def toggle_row(self, filename):
        table = self.app.table
        row = next(item for item in table.get_children() if table.set(item, "source") == filename)
        self.assertIn("include", table.cget("columns"))
        table.see(row)
        self.root.update()
        x, y, width, height = table.bbox(row, "include")
        # Model separate clicks with time to click the page button in between.
        self.event_time += 1000
        table.event_generate("<Button-1>", x=x + width // 2, y=y + height // 2, time=self.event_time)
        self.root.update()

    def test_downloads_profile_uses_categories_and_keeps_explicit_source(self):
        (self.source / "agenda.pdf").write_bytes(b"document")
        self.app.extensions.set("mp4")
        self.app.grouping.set("Jeden folder")

        self.click("Pobrane według kategorii")
        self.app.preview()
        self.await_ready()

        self.assertEqual([transfer.destination for transfer in self.app.plan],
                         [self.destination / "Dokumenty" / "agenda.pdf"])
        self.assertEqual(Path(self.app.source.get()), self.source)
        self.assertEqual(self.app.operation.get(), "Kopiuj")

    def test_photos_profile_uses_original_month_and_filters_out_documents(self):
        album = self.source / "Album"
        album.mkdir()
        photo = album / "Photo.JPG"
        exif = Image.Exif()
        exif[ExifTags.IFD.Exif] = {ExifTags.Base.DateTimeOriginal: "2004:04:06 12:00:00"}
        with Image.new("RGB", (2, 2), "white") as image:
            image.save(photo, exif=exif)
        recently_modified = datetime(2024, 10, 5, 12).timestamp()
        os.utime(photo, (recently_modified, recently_modified))
        (self.source / "agenda.pdf").write_bytes(b"document")

        self.click("Zdjęcia według daty")
        self.app.preview()
        self.await_ready()

        transfer, = self.app.plan
        self.assertEqual(transfer.source.name, "Photo.JPG")
        self.assertEqual(transfer.destination.relative_to(self.destination).parts, ("2004", "04", "Photo.JPG"))

        self.app.grouping.set("Data modyfikacji")
        self.app.preview()
        self.await_ready()
        transfer, = self.app.plan
        self.assertEqual(transfer.destination.relative_to(self.destination).parts, ("2024", "10", "Photo.JPG"))

    def test_photos_profile_separates_images_missing_original_date(self):
        photo = self.source / "Without-date.jpg"
        with Image.new("RGB", (2, 2), "white") as image:
            image.save(photo)

        self.click("Zdjęcia według daty")
        self.app.preview()
        self.await_ready()

        transfer, = self.app.plan
        self.assertEqual(transfer.destination.relative_to(self.destination).parts,
                         ("Bez daty w metadanych", "Without-date.jpg"))

    def test_copy_preserves_choices_across_preview_pages(self):
        for number in range(201):
            (self.source / f"photo-{number:03}.jpg").write_bytes(b"image")
        self.app.preview()
        self.await_ready()

        self.click("Odznacz wszystkie")
        self.toggle_row("photo-000.jpg")
        self.toggle_row("photo-001.jpg")
        self.click("Następna")
        self.toggle_row("photo-200.jpg")
        self.click("Poprzednia")
        self.toggle_row("photo-000.jpg")
        self.app.execute()
        self.await_ready()

        photos = sorted((self.destination / "Zdjęcia").glob("*.jpg"))
        self.assertEqual([photo.name for photo in photos], ["photo-001.jpg", "photo-200.jpg"])

    def test_clearing_selection_disables_transfer_and_select_all_restores_it(self):
        (self.source / "file.txt").write_bytes(b"note")
        self.app.preview()
        self.await_ready()

        self.click("Odznacz wszystkie")
        self.assertTrue(self.app.execute_button.instate(["disabled"]))
        self.assertFalse(self.destination.exists())

        self.click("Zaznacz wszystkie")
        self.assertFalse(self.app.execute_button.instate(["disabled"]))


if __name__ == "__main__":
    unittest.main()
