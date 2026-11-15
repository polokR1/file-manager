import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from organizer import Transfer, should_skip, transfer_file


@dataclass(frozen=True)
class HistoryBatch:
    id: int
    created_at: str
    move: bool
    completed: int
    remaining: int
    uncertain: int


@dataclass(frozen=True)
class UndoIssue:
    path: Path
    message: str


@dataclass(frozen=True)
class UndoResult:
    restored: int
    issues: tuple[UndoIssue, ...]


class HistoryError(OSError):
    pass


def _optional_stat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _identity(info: os.stat_result) -> tuple[int, int]:
    if not info.st_ino:
        raise ValueError("System plików nie udostępnia identyfikatora potrzebnego do cofnięcia operacji.")
    return info.st_dev, info.st_ino


def _directory_signature(path: Path) -> str:
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    if (not stat.S_ISDIR(info.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or path.resolve(strict=True) != path):
        raise ValueError("Folder został przekierowany albo zastąpiony.")
    return json.dumps(_identity(info))


def _file_stat(path: Path) -> os.stat_result:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or should_skip(info):
        raise ValueError("Ścieżka nie prowadzi do zwykłego pliku.")
    return info


def _file_signature(info: os.stat_result) -> str:
    return json.dumps((*_identity(info), info.st_size, info.st_mtime_ns, info.st_ctime_ns))


def _assert_vacant(path: Path) -> None:
    if _optional_stat(path) is not None:
        raise FileExistsError(f"Ścieżka jest już zajęta: {path}")


class TransferHistory:
    def __init__(self, path: Path | None = None):
        if path is None:
            app_data = os.environ.get("LOCALAPPDATA")
            root = Path(app_data) if app_data else Path.home() / "AppData" / "Local"
            path = root / "PoloQP" / "FileOrganizer" / "history.sqlite3"
        self.path = path

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        try:
            with closing(sqlite3.connect(self.path, timeout=10, isolation_level="IMMEDIATE")) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA foreign_keys = ON")
                with db:
                    yield db
        except sqlite3.Error as error:
            raise HistoryError(f"Nie udało się zapisać lub odczytać historii. {error}") from error

    def start_batch(self, move: bool) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS batches (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    move INTEGER NOT NULL CHECK (move IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS transfers (
                    id INTEGER PRIMARY KEY,
                    batch_id INTEGER NOT NULL REFERENCES batches (id),
                    source TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    source_directory TEXT NOT NULL,
                    destination_directory TEXT,
                    destination_file TEXT,
                    state TEXT NOT NULL CHECK (state IN ('pending', 'failed', 'completed', 'undo_pending', 'undone'))
                );
                CREATE INDEX IF NOT EXISTS transfers_batch ON transfers (batch_id, id);
            """)
            cursor = db.execute("INSERT INTO batches (created_at, move) VALUES (?, ?)",
                                (datetime.now().astimezone().isoformat(sep=" ", timespec="seconds"), move))
            return cursor.lastrowid

    def transfer(self, batch_id: int, item: Transfer, move: bool) -> None:
        journal = self.path.resolve()
        journal_paths = {
            journal.with_name(journal.name + suffix) for suffix in ("", "-wal", "-shm", "-journal")
        }
        if item.source in journal_paths or item.destination in journal_paths:
            raise ValueError("Historia operacji jest plikiem aplikacji i pozostaje na swoim miejscu.")
        original_dir = _directory_signature(item.source.parent)
        original = _file_stat(item.source)
        signature = _file_signature(original)
        if item.destination.parent.resolve() != item.destination.parent:
            raise ValueError("Folder docelowy został przekierowany od czasu podglądu.")
        _assert_vacant(item.destination)
        destination_dir = (_directory_signature(item.destination.parent)
                           if _optional_stat(item.destination.parent) is not None else None)

        # Commit the intent before touching the filesystem so interrupted moves remain visible.
        with self._connection() as db:
            batch = db.execute("SELECT move FROM batches WHERE id = ?", (batch_id,)).fetchone()
            if batch is None or bool(batch["move"]) != move:
                raise ValueError("Operacja nie pasuje do wskazanego wpisu historii.")
            cursor = db.execute("""
                INSERT INTO transfers (batch_id, source, destination, source_directory, state)
                VALUES (?, ?, ?, ?, 'pending')
            """, (batch_id, str(item.source), str(item.destination), original_dir))
            entry = cursor.lastrowid

        try:
            if _file_signature(_file_stat(item.source)) != signature:
                raise ValueError("Plik źródłowy zmienił się od rozpoczęcia operacji.")
            if _directory_signature(item.source.parent) != original_dir or (
                destination_dir is not None and _directory_signature(item.destination.parent) != destination_dir
            ):
                raise ValueError("Jeden z folderów został zastąpiony od rozpoczęcia operacji.")
        except (OSError, ValueError):
            self._set_state(entry, "failed")
            raise

        try:
            transfer_file(item, move)
        except (OSError, ValueError) as error:
            if self._move_unchanged(item, original_dir, signature, destination_dir):
                self._set_state(entry, "failed")
                raise
            raise HistoryError(
                f"Stan pliku po operacji jest niepewny. Sprawdź oba foldery i wpis w historii. {error}"
            ) from error

        try:
            result = _file_stat(item.destination)
            actual_dir = _directory_signature(item.destination.parent)
            expected_identity = (move and os.name == "nt" and result.st_dev == original.st_dev)
            if ((result.st_size, result.st_mtime_ns) != (original.st_size, original.st_mtime_ns)
                    or (expected_identity and _identity(result) != _identity(original))
                    or (destination_dir is not None and destination_dir != actual_dir)
                    or _directory_signature(item.source.parent) != original_dir
                    or (move and _optional_stat(item.source) is not None)):
                raise ValueError("Stan plików uległ zmianie podczas potwierdzania operacji.")
            with self._connection() as db:
                db.execute("""
                    UPDATE transfers SET state = 'completed', destination_directory = ?, destination_file = ?
                    WHERE id = ?
                """, (actual_dir, _file_signature(result), entry))
        except (OSError, ValueError) as error:
            raise HistoryError(f"Sprawdź oba foldery. Nie udało się potwierdzić operacji w historii. {error}") from error

    @staticmethod
    def _move_unchanged(item: Transfer, source_dir: str, original: str,
                        destination_dir: str | None) -> bool:
        try:
            return (
                _file_signature(_file_stat(item.source)) == original
                and _directory_signature(item.source.parent) == source_dir
                and _optional_stat(item.destination) is None
                and (destination_dir is None or _directory_signature(item.destination.parent) == destination_dir)
            )
        except (OSError, ValueError):
            return False

    def _set_state(self, entry: int, state: str) -> None:
        with self._connection() as db:
            db.execute("UPDATE transfers SET state = ? WHERE id = ?", (state, entry))

    def batches(self) -> list[HistoryBatch]:
        if _optional_stat(self.path) is None:
            return []
        with self._connection() as db:
            rows = db.execute("""
                SELECT b.id, b.created_at, b.move,
                       COUNT(CASE WHEN t.state IN ('completed', 'undo_pending', 'undone') THEN 1 END) AS completed,
                       COUNT(CASE WHEN b.move = 1 AND t.state = 'completed' THEN 1 END) AS remaining,
                       COUNT(CASE WHEN t.state IN ('pending', 'undo_pending') THEN 1 END) AS uncertain
                FROM batches AS b LEFT JOIN transfers AS t ON b.id = t.batch_id
                GROUP BY b.id ORDER BY b.id DESC
            """).fetchall()
        return [HistoryBatch(row["id"], row["created_at"], bool(row["move"]), row["completed"], row["remaining"],
                             row["uncertain"]) for row in rows]

    def undo(self, batch_id: int) -> UndoResult:
        if _optional_stat(self.path) is None:
            return UndoResult(0, ())
        with self._connection() as db:
            entries = db.execute("""
                SELECT t.*, b.move FROM transfers AS t JOIN batches AS b ON b.id = t.batch_id
                WHERE b.id = ? AND (t.state IN ('pending', 'undo_pending') OR (b.move = 1 AND t.state = 'completed'))
                ORDER BY t.id DESC
            """, (batch_id,)).fetchall()

        restored = 0
        issues = []
        for entry in entries:
            source = Path(entry["source"])
            destination = Path(entry["destination"])
            if entry["state"] != "completed":
                issues.append(UndoIssue(source, f"Przerwana operacja. Sprawdź ręcznie obie ścieżki, w tym: {destination}"))
                continue
            try:
                if self._undo_file(entry, Transfer(destination, source)):
                    restored += 1
            except (OSError, ValueError) as error:
                issues.append(UndoIssue(source, str(error)))
        return UndoResult(restored, tuple(issues))

    def _undo_file(self, entry: sqlite3.Row, item: Transfer) -> bool:
        # The conditional claim prevents another process from undoing the same entry twice.
        with self._connection() as db:
            claimed = db.execute("UPDATE transfers SET state = 'undo_pending' WHERE id = ? AND state = 'completed'",
                                 (entry["id"],)).rowcount
        if not claimed:
            return False
        try:
            _assert_vacant(item.destination)
            if (_directory_signature(item.source.parent) != entry["destination_directory"]
                    or _directory_signature(item.destination.parent) != entry["source_directory"]):
                raise ValueError("Co najmniej jeden folder został zastąpiony od przeniesienia.")
            if _file_signature(_file_stat(item.source)) != entry["destination_file"]:
                raise ValueError("Plik został zmieniony albo zastąpiony od przeniesienia.")
        except (OSError, ValueError):
            self._set_state(entry["id"], "completed")
            raise

        try:
            transfer_file(item, move=True)
        except (OSError, ValueError):
            if self._move_unchanged(item, entry["destination_directory"], entry["destination_file"],
                                    entry["source_directory"]):
                self._set_state(entry["id"], "completed")
            raise

        try:
            self._set_state(entry["id"], "undone")
        except HistoryError as error:
            raise HistoryError(f"Plik został przywrócony, ale historia nie została zaktualizowana. {error}") from error
        return True
