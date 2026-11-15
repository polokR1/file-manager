import errno
import os
import re
import shutil
import stat
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time

from file_dates import file_creation_date, original_file_date
from operation_feedback import OperationIssue


CATEGORIES = {
    "Zdjęcia": "jpg jpeg png gif bmp tif tiff webp heic heif svg avif raw dng cr2 nef",
    "Dokumenty": "pdf doc docx odt txt rtf md xls xlsx ods csv ppt pptx odp epub",
    "Wideo": "mp4 mov mkv avi webm wmv m4v mpg mpeg 3gp",
    "Audio": "mp3 wav flac aac m4a ogg opus wma",
    "Archiwa": "zip rar 7z tar gz bz2 xz tgz",
    "Instalatory": "exe msi msix appx msixbundle appxbundle",
}
CATEGORY_BY_SUFFIX = {
    f".{extension}": category
    for category, extensions in CATEGORIES.items()
    for extension in extensions.split()
}


@dataclass(frozen=True)
class Transfer:
    source: Path
    destination: Path


def parse_extensions(text: str) -> tuple[str, ...]:
    parts = [part for part in re.split(r"[,;\s]+", text.strip().casefold()) if part]
    if parts == ["*"]:
        return ("*",)
    if not parts or any(
        not re.fullmatch(r"\.?[\w+-]+(?:\.[\w+-]+)*", part) for part in parts
    ):
        raise ValueError("Wpisz rozszerzenia, np. jpg, pdf, albo * dla wszystkich plików.")
    return tuple(dict.fromkeys("." + part.lstrip(".") for part in parts))


def should_skip(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(
        attributes & (stat.FILE_ATTRIBUTE_REPARSE_POINT | stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM)
    )


def raise_scan_error(error: OSError) -> None:
    raise error


def scan_files(
    source: Path, extensions: tuple[str, ...], recursive: bool, exclude: Path | None,
    *, on_error: Callable[[OSError], None] = raise_scan_error,
    checkpoint: Callable[[], None] = lambda: None,
    excluded_folders: tuple[str, ...] = (),
) -> Iterator[tuple[Path, os.stat_result]]:
    excluded_names = {name.casefold() for name in excluded_folders}
    for folder, dirs, filenames in os.walk(source, onerror=on_error):
        checkpoint()
        current = Path(folder)
        children = []
        if recursive:
            for name in sorted(dirs, key=str.casefold):
                checkpoint()
                child = current / name
                if child == exclude or name.casefold() in excluded_names:
                    continue
                try:
                    if not should_skip(child.lstat()):
                        children.append(name)
                except OSError as error:
                    on_error(error)
        dirs[:] = children

        for name in sorted(filenames, key=str.casefold):
            checkpoint()
            if extensions != ("*",) and not name.casefold().endswith(extensions):
                continue
            path = current / name
            try:
                info = path.lstat()
            except OSError as error:
                on_error(error)
                continue
            if stat.S_ISREG(info.st_mode) and not should_skip(info):
                yield path, info


def category_of(path: Path) -> str:
    return CATEGORY_BY_SUFFIX.get(path.suffix.casefold(), "Inne")


def modification_date(timestamp: float) -> datetime:
    try:
        return datetime.fromtimestamp(timestamp)
    except (OSError, OverflowError, ValueError):
        # The Windows C runtime rejects some valid NTFS dates (including pre-1970).
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=timestamp)


def group_folder(path: Path, info: os.stat_result, *, by_category, by_type, by_date, by_size,
                 by_original_date, by_creation_date) -> Path:
    if by_category:
        return Path(category_of(path))
    if by_type:
        label = re.sub(r"[^\w+-]", "_", path.suffix.lstrip(".").upper())
        if re.fullmatch(r"CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³]", label):
            label = f"_{label}"
        return Path(label or "Bez rozszerzenia")
    if by_date or by_original_date or by_creation_date:
        if by_original_date:
            date = original_file_date(path, info)
            if date is None:
                return Path("Bez daty w metadanych")
        elif by_creation_date:
            date = file_creation_date(info)
            if date is None:
                return Path("Bez daty utworzenia")
        else:
            date = modification_date(info.st_mtime)
        return Path(f"{date.year:04d}", f"{date.month:02d}")
    if by_size:
        if info.st_size < 1024**2:
            return Path("Poniżej 1 MiB")
        if info.st_size < 100 * 1024**2:
            return Path("1–100 MiB")
        return Path("100 MiB i więcej")
    return Path()


def build_plan(
    source: Path,
    destination: Path,
    extensions: tuple[str, ...],
    recursive: bool,
    group_by_type: bool,
    *,
    group_by_category: bool = False,
    group_by_date: bool = False,
    group_by_size: bool = False,
    group_by_original_date: bool = False,
    group_by_creation_date: bool = False,
    skip_existing: bool = False,
    min_age_days: int = 0,
    excluded_folders: tuple[str, ...] = (),
    issues: list[OperationIssue] | None = None,
    checkpoint: Callable[[], None] = lambda: None,
) -> list[Transfer]:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not stat.S_ISDIR(source.stat().st_mode):
        raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), str(source))
    if destination.exists() and not destination.is_dir():
        raise ValueError("Ścieżka docelowa prowadzi do pliku. Wybierz folder.")
    if source == destination:
        raise ValueError("Wybierz inny folder docelowy niż folder źródłowy.")

    plan = []
    reserved = {}
    blocked = set()
    last_allowed_change = time() - min_age_days * 86400

    def record_scan_error(error: OSError) -> None:
        if issues is None:
            raise error
        issues.append(OperationIssue(Path(error.filename) if error.filename else source, error))

    for original, info in scan_files(source, extensions, recursive, destination, on_error=record_scan_error,
                                     checkpoint=checkpoint, excluded_folders=excluded_folders):
        if min_age_days and info.st_mtime > last_allowed_change:
            continue
        target_folder = destination / group_folder(
            original, info, by_category=group_by_category, by_type=group_by_type,
            by_date=group_by_date, by_size=group_by_size,
            by_original_date=group_by_original_date, by_creation_date=group_by_creation_date,
        )
        if target_folder in blocked:
            continue
        if target_folder not in reserved:
            try:
                if target_folder.resolve() != target_folder or target_folder == source or (
                    target_folder.is_relative_to(source) and not destination.is_relative_to(source)
                ):
                    raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(target_folder))
                try:
                    reserved[target_folder] = {item.name.casefold() for item in target_folder.iterdir()}
                except FileNotFoundError:
                    reserved[target_folder] = set()
            except OSError as error:
                if issues is None:
                    raise
                issues.append(OperationIssue(target_folder, error))
                blocked.add(target_folder)
                continue

        names = reserved[target_folder]
        candidate = original.name
        if skip_existing and candidate.casefold() in names:
            continue
        index = 1
        while candidate.casefold() in names:
            candidate = f"{original.stem}_{index}{original.suffix}"
            index += 1
        names.add(candidate.casefold())
        plan.append(Transfer(original, target_folder / candidate))
    return plan


def transfer_file(transfer: Transfer, move: bool) -> None:
    source, destination = transfer.source, transfer.destination
    if should_skip(source.lstat()) or source.resolve(strict=True) != source or not source.is_file():
        raise ValueError("Plik źródłowy został zastąpiony lub przekierowany.")
    if destination.parent.resolve() != destination.parent:
        raise ValueError("Folder docelowy został przekierowany od czasu podglądu.")
    destination.parent.mkdir(parents=True, exist_ok=True)

    if move and os.name == "nt":
        # Windows rename is atomic on the same volume and fails if the target exists.
        try:
            os.rename(source, destination)
            return
        except OSError as error:
            if error.errno != errno.EXDEV and getattr(error, "winerror", None) != 17:
                raise

    # Copy into a private directory first. On Windows copy2 uses the native copy
    # operation, which can preserve NTFS streams (e.g. download security labels).
    with TemporaryDirectory(prefix=".file-organizer-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "payload"
        original = source.stat()
        shutil.copy2(source, staged)
        current = source.stat()
        if not os.path.samestat(original, current) or (
            original.st_size, original.st_mtime_ns
        ) != (current.st_size, current.st_mtime_ns):
            raise OSError("Plik źródłowy zmienił się podczas kopiowania.")

        copied = staged.stat()
        if os.name == "nt":
            os.rename(staged, destination)
        else:
            os.link(staged, destination)

        if move:
            try:
                source.unlink()
            except OSError:
                if destination.exists() and os.path.samestat(copied, destination.lstat()):
                    try:
                        destination.unlink()
                    except PermissionError:
                        if os.name != "nt":
                            raise
                        destination.chmod(stat.S_IWRITE)
                        destination.unlink()
                raise


if __name__ == "__main__":
    from organizer_ui import main

    main()
