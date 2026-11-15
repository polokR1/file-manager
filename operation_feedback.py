import errno
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryFile


@dataclass(frozen=True)
class OperationIssue:
    path: Path
    error: Exception


def describe_issue(error: Exception, language: str = "pl") -> str:
    number = getattr(error, "errno", None)
    winerror = getattr(error, "winerror", None)
    if winerror in {32, 33}:
        message = ("Plik jest używany. Zamknij aplikację, która go otworzyła, i ponów próbę.",
                   "The file is in use. Close the app that has it open and retry.")
    elif isinstance(error, PermissionError) or number in {errno.EACCES, errno.EPERM}:
        message = ("Brak dostępu. Sprawdź uprawnienia do pliku lub wybierz inny folder.",
                   "Access denied. Check the file permissions or choose another folder.")
    elif isinstance(error, FileExistsError):
        message = ("Nazwa docelowa jest już zajęta. Zwolnij nazwę lub przygotuj nowy podgląd.",
                   "The destination name is already in use. Free the name or prepare a new preview.")
    elif isinstance(error, FileNotFoundError):
        message = ("Plik lub folder jest niedostępny. Sprawdź ścieżkę i połączenie z dyskiem.",
                   "The file or folder is unavailable. Check the path and drive connection.")
    elif number == errno.ENOSPC or winerror in {39, 112}:
        message = ("Brak wystarczającego miejsca na dysku docelowym.",
                   "There is not enough space on the destination drive.")
    elif isinstance(error, NotADirectoryError):
        message = ("Ścieżka docelowa nie prowadzi do folderu.", "The destination path is not a folder.")
    else:
        return str(error)
    return message[language == "en"]


def _nearest_directory(folder: Path) -> Path:
    while True:
        try:
            folder.lstat()
            break
        except FileNotFoundError:
            if folder == folder.parent:
                raise
            folder = folder.parent

    if not folder.is_dir():
        raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), str(folder))
    if folder.resolve(strict=True) != folder:
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(folder))
    return folder


def check_destination(destination: Path, plan, *, moving: bool) -> None:
    """Check free space and that at least one planned target can be written."""
    if not plan:
        return
    folder = _nearest_directory(destination)

    target_device = folder.stat().st_dev
    required = 0
    for transfer in plan:
        try:
            source = transfer.source.stat()
        except OSError:
            # Unavailable sources are reported per file by the transfer worker.
            continue
        if not moving or os.name != "nt" or not target_device or source.st_dev != target_device:
            required += source.st_size
    if required and required > shutil.disk_usage(folder).free:
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), str(destination))

    first_error = None
    tested = set()
    for target in dict.fromkeys(transfer.destination.parent for transfer in plan):
        try:
            nearest = _nearest_directory(target)
            if nearest in tested:
                continue
            tested.add(nearest)
            with TemporaryFile(dir=nearest):
                return
        except OSError as error:
            if first_error is None:
                first_error = error
    if first_error is not None:
        raise first_error
