import hashlib
import os
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from organizer import category_of, scan_files


@dataclass(frozen=True)
class FileInfo:
    path: Path
    size: int
    category: str
    modified: float
    identity: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class CategorySummary:
    name: str
    count: int
    total_bytes: int


@dataclass(frozen=True)
class DuplicateGroup:
    files: tuple[FileInfo, ...]

    @property
    def redundant_bytes(self) -> int:
        return self.files[0].size * (len(self.files) - 1)


@dataclass(frozen=True)
class ScanIssue:
    path: Path
    message: str


@dataclass(frozen=True)
class AnalysisReport:
    files: tuple[FileInfo, ...]
    total_bytes: int
    categories: tuple[CategorySummary, ...]
    duplicates: tuple[DuplicateGroup, ...]
    issues: tuple[ScanIssue, ...]


class AnalysisCancelled(Exception):
    pass


def fingerprint(path: Path, expected: os.stat_result, checkpoint: Callable[[], None]) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            checkpoint()
            digest.update(chunk)
        for current in (os.fstat(file.fileno()), path.lstat()):
            if not os.path.samestat(expected, current) or (
                expected.st_size, expected.st_mtime_ns
            ) != (current.st_size, current.st_mtime_ns):
                raise OSError("Plik zmienił się podczas analizy.")
    return digest.digest()


def analyze_files(
    source: Path, extensions: tuple[str, ...], recursive: bool,
    *, exclude: Path | None = None, find_duplicates: bool = True,
    cancelled: Callable[[], bool] = lambda: False,
) -> AnalysisReport:
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise ValueError("Wybierz istniejący folder źródłowy.")
    if exclude is not None:
        exclude = exclude.expanduser().resolve()

    issues = []

    def check_cancellation():
        if cancelled():
            raise AnalysisCancelled()

    def record_scan_error(error: OSError):
        issues.append(ScanIssue(Path(error.filename) if error.filename else source, str(error)))

    files = []
    by_category = defaultdict(list)
    by_size = defaultdict(list)
    check_cancellation()
    for path, stats in scan_files(source, extensions, recursive, exclude,
                                  on_error=record_scan_error, checkpoint=check_cancellation):
        identity = (stats.st_dev, stats.st_ino, stats.st_mtime_ns, getattr(stats, "st_birthtime_ns", stats.st_ctime_ns))
        entry = FileInfo(path, stats.st_size, category_of(path), stats.st_mtime, identity)
        files.append(entry)
        by_category[entry.category].append(entry.size)
        if find_duplicates:
            by_size[entry.size].append((entry, stats))

    duplicates = []
    for candidates in by_size.values():
        check_cancellation()
        if len(candidates) < 2:
            continue
        by_hash = defaultdict(list)
        identities = set()
        for entry, stats in candidates:
            check_cancellation()
            identity = (stats.st_dev, stats.st_ino)
            if stats.st_ino and identity in identities:
                continue
            try:
                digest = fingerprint(entry.path, stats, check_cancellation)
            except OSError as error:
                issues.append(ScanIssue(entry.path, str(error)))
                continue
            by_hash[digest].append(entry)
            if stats.st_ino:
                identities.add(identity)
        duplicates.extend(DuplicateGroup(tuple(group)) for group in by_hash.values() if len(group) > 1)

    check_cancellation()
    categories = (CategorySummary(name, len(sizes), sum(sizes)) for name, sizes in by_category.items())
    return AnalysisReport(
        tuple(sorted(files, key=lambda entry: -entry.size)),
        sum(entry.size for entry in files),
        tuple(sorted(categories, key=lambda category: (-category.total_bytes, category.name))),
        tuple(sorted(duplicates, key=lambda group: -group.redundant_bytes)),
        tuple(issues),
    )
