from collections.abc import Callable
from contextlib import ExitStack
import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import stat
from uuid import UUID

from file_analysis import FileInfo


@dataclass(frozen=True)
class CleanupResult:
    recycled: tuple[Path, ...]
    failures: tuple[tuple[Path, str], ...]


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_mtime_ns, getattr(info, "st_birthtime_ns", info.st_ctime_ns)


def _check_snapshot(entry: FileInfo, info: os.stat_result) -> None:
    attributes = getattr(info, "st_file_attributes", 0)
    if not stat.S_ISREG(info.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise OSError("Plik jest dowiązaniem albo nie jest zwykłym plikiem.")
    if not info.st_ino or entry.identity != _identity(info) or entry.size != info.st_size:
        raise OSError("Plik zmienił się od czasu analizy. Uruchom analizę ponownie.")


def _check_path(entry: FileInfo, source: Path) -> None:
    path = entry.path
    if not path.is_absolute() or path == source or not path.is_relative_to(source) or ".." in path.parts:
        raise OSError("Plik jest poza analizowanym folderem.")
    if any(part.casefold() == "$recycle.bin" for part in path.parts):
        raise OSError("Plik jest już w Koszu.")
    for parent in path.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError("Ścieżka prowadzi przez dowiązanie lub niedostępny folder.")
    _check_snapshot(entry, path.lstat())


def _open_for_compare(path: Path, *, share_delete: bool):
    if os.name != "nt":
        return path.open("rb")

    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel.CreateFileW
    create_file.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create_file.restype = wintypes.HANDLE
    generic_read = 0x80000000
    share_read = 0x1
    open_existing = 3
    open_reparse_point = 0x00200000
    handle = create_file(str(path), generic_read, share_read | (0x4 if share_delete else 0), None,
                         open_existing, open_reparse_point, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle(handle)
        raise
    return os.fdopen(descriptor, "rb")


def _compare_and_recycle(keeper: FileInfo, copy: FileInfo, source: Path) -> None:
    if keeper.path == copy.path or (
        keeper.identity and copy.identity and keeper.identity[:2] == copy.identity[:2]
    ):
        raise OSError("Wybrany plik jest oryginałem lub wskazuje na ten sam plik.")
    _check_path(keeper, source)
    _check_path(copy, source)
    with _open_for_compare(keeper.path, share_delete=False) as original, \
            _open_for_compare(copy.path, share_delete=True) as candidate:
        def verify():
            for entry, file in ((keeper, original), (copy, candidate)):
                _check_path(entry, source)
                _check_snapshot(entry, os.fstat(file.fileno()))

        verify()
        while True:
            left = original.read(1024 * 1024)
            right = candidate.read(1024 * 1024)
            if left != right:
                raise OSError("Zawartość plików jest różna.")
            if not left:
                break
        _recycle_file(copy.path, verify, candidate.fileno())


def recycle_duplicates(keeper: FileInfo, copies: tuple[FileInfo, ...], *, source: Path) -> CleanupResult:
    source = source.expanduser().absolute()
    recycled = []
    failures = []
    for copy in copies:
        try:
            _compare_and_recycle(keeper, copy, source)
        except OSError as error:
            failures.append((copy.path, str(error)))
        else:
            recycled.append(copy.path)
    return CleanupResult(tuple(recycled), tuple(failures))


def _recycle_file(path: Path, verify: Callable[[], None], descriptor: int) -> None:
    if os.name != "nt":
        raise OSError("Przenoszenie do Kosza jest dostępne tylko w systemie Windows.")

    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDriveTypeW.argtypes = (wintypes.LPCWSTR,)
    kernel.GetDriveTypeW.restype = wintypes.UINT
    if len(path.drive) != 2 or kernel.GetDriveTypeW(path.anchor) != 3:  # DRIVE_FIXED
        raise OSError("Kosz jest obsługiwany tylko na lokalnych dyskach stałych.")

    class StreamData(ctypes.Structure):
        _fields_ = [("size", ctypes.c_longlong), ("name", wintypes.WCHAR * 296)]

    kernel.FindFirstStreamW.argtypes = (wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(StreamData), wintypes.DWORD)
    kernel.FindFirstStreamW.restype = wintypes.HANDLE
    kernel.FindNextStreamW.argtypes = (wintypes.HANDLE, ctypes.POINTER(StreamData))
    kernel.FindNextStreamW.restype = wintypes.BOOL
    kernel.FindClose.argtypes = (wintypes.HANDLE,)
    data = StreamData()
    streams = kernel.FindFirstStreamW(str(path), 0, ctypes.byref(data), 0)
    if streams == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if data.name != "::$DATA" or kernel.FindNextStreamW(streams, ctypes.byref(data)):
            raise OSError("Plik ma dodatkowe strumienie danych. Sprawdź go ręcznie.")
        if ctypes.get_last_error() != 38:  # ERROR_HANDLE_EOF
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.FindClose(streams)

    code = _shell_recycle(path, verify)
    # Some Shell versions omit the recycled-item pointer even when the transfer succeeded.
    # The handle still refers to the exact file we compared, regardless of its new name.
    final_path = kernel.GetFinalPathNameByHandleW
    final_path.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    final_path.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    written = final_path(msvcrt.get_osfhandle(descriptor), buffer, len(buffer), 0)
    if written and written < len(buffer):
        result = Path(buffer.value.removeprefix("\\\\?\\"))
        if result.drive.casefold() == path.drive.casefold() and len(result.parts) >= 4:
            if result.parts[1].casefold() == "$recycle.bin":
                return
    raise OSError(f"Windows nie potwierdził przeniesienia pliku do Kosza (HRESULT 0x{code:08X}).")


def _shell_recycle(path: Path, verify: Callable[[], None]) -> int:
    # ITransferSource.RecycleItem explicitly moves to the virtual Recycle Bin.
    # There is deliberately no call to RemoveItem or general-purpose DeleteItem.
    from ctypes import wintypes

    pointer = ctypes.c_void_p
    hresult = ctypes.c_long
    guid = ctypes.c_ubyte * 16
    shell_item_id = guid.from_buffer_copy(UUID("43826d1e-e718-42ee-bc55-a1e261c37bfe").bytes_le)
    transfer_id = guid.from_buffer_copy(UUID("00adb003-bde9-45c6-8e29-d09f9353e108").bytes_le)
    transfer_handler = guid.from_buffer_copy(UUID("d5e346a1-f753-4932-b403-4574800e2498").bytes_le)
    recycle_folder_id = guid.from_buffer_copy(UUID("b7534046-3ecb-4c18-be4e-64cd4cb7d6ac").bytes_le)
    ole = ctypes.WinDLL("ole32")
    shell = ctypes.WinDLL("shell32")
    ole.CoInitializeEx.argtypes = (pointer, wintypes.DWORD)
    ole.CoInitializeEx.restype = hresult
    ole.CoUninitialize.argtypes = ()
    shell.SHCreateItemFromParsingName.argtypes = (
        wintypes.LPCWSTR, pointer, ctypes.POINTER(guid), ctypes.POINTER(pointer),
    )
    shell.SHCreateItemFromParsingName.restype = hresult
    shell.SHGetKnownFolderItem.argtypes = (
        ctypes.POINTER(guid), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(guid), ctypes.POINTER(pointer),
    )
    shell.SHGetKnownFolderItem.restype = hresult

    def check(code, operation):
        if code < 0:
            raise OSError(f"{operation} (HRESULT 0x{code & 0xFFFFFFFF:08X}).")

    def method(obj, slot, result_type, *args):
        table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(pointer))).contents
        return ctypes.WINFUNCTYPE(result_type, pointer, *args)(table[slot])

    def release(obj):
        method(obj, 2, wintypes.ULONG)(obj)  # IUnknown::Release

    check(ole.CoInitializeEx(None, 2), "Nie udało się uruchomić usług systemu Windows")  # STA
    with ExitStack() as stack:
        stack.callback(ole.CoUninitialize)
        parent, item, bin_item, transfer, new_item = (pointer() for _ in range(5))
        for shell_path, result in ((path.parent, parent), (path, item)):
            check(shell.SHCreateItemFromParsingName(str(shell_path), None, ctypes.byref(shell_item_id),
                                                    ctypes.byref(result)),
                  "Windows nie rozpoznaje ścieżki")
            stack.callback(release, result)
        check(shell.SHGetKnownFolderItem(ctypes.byref(recycle_folder_id), 0, None,
                                        ctypes.byref(shell_item_id), ctypes.byref(bin_item)),
              "Nie udało się otworzyć Kosza")
        stack.callback(release, bin_item)

        bind = method(parent, 3, hresult, pointer, ctypes.POINTER(guid), ctypes.POINTER(guid),
                      ctypes.POINTER(pointer))  # IShellItem::BindToHandler
        check(bind(parent, None, ctypes.byref(transfer_handler), ctypes.byref(transfer_id), ctypes.byref(transfer)),
              "Windows nie udostępnił przenoszenia do Kosza")
        stack.callback(release, transfer)
        recycle = method(transfer, 8, hresult, pointer, pointer, wintypes.DWORD,
                         ctypes.POINTER(pointer))  # ITransferSource::RecycleItem
        verify()
        code = recycle(transfer, item, bin_item, 0, ctypes.byref(new_item))
        if new_item:
            stack.callback(release, new_item)
        check(code, "Windows odmówił przeniesienia pliku do Kosza")
        return code
