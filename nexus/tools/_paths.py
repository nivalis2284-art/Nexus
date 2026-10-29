"""Fail-closed local paths and handle-based opens; no authorization decisions."""
import errno
import os
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path


class FileAccessDenied(PermissionError):
    """A path is outside the supplied grants or uses an unsafe alias."""


def is_link(info: os.stat_result) -> bool:
    """True for any alias that redirects elsewhere.

    ``S_ISLNK`` alone misses Windows junctions and mount points: those report as
    ordinary directories (``S_ISDIR``) and only betray themselves through
    ``FILE_ATTRIBUTE_REPARSE_POINT``. Both halves are required, so this is the one
    predicate every containment check should share.
    """
    return bool(
        stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
    )


def reject_link(info: os.stat_result) -> None:
    if is_link(info):
        raise FileAccessDenied("Symlinks, junctions and reparse points are not allowed")


def regular_file(info: os.stat_result) -> None:
    reject_link(info)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise FileAccessDenied("Only regular files with one link are allowed")


@contextmanager
def open_file(path: Path, operation: str):
    """Open without following links; validate before any read or truncation.

    Windows holds every ancestor without FILE_SHARE_DELETE while using the file.
    POSIX walks directory descriptors with O_NOFOLLOW. Neither permits a linked
    leaf. These checks do not replace an OS sandbox for arbitrary code execution.
    """
    with ExitStack() as stack:
        if os.name == "nt":
            fd = _windows_open(path, operation, stack)
        else:
            fd = _posix_open(path, operation, stack)
        regular_file(os.fstat(fd))
        if operation == "read":
            # closefd=False: ExitStack owns the OS descriptor, including
            # validation failures.
            with os.fdopen(fd, "rb", closefd=False) as stream:
                yield stream
        else:
            # "wb" on an already-open descriptor does NOT truncate; it only sets
            # the mode. A shorter write used to leave the previous tail behind,
            # which reads as silent corruption of the file. Truncate the held
            # handle and rewind before any bytes land.
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            with os.fdopen(fd, "wb", closefd=False) as stream:
                yield stream


@contextmanager
def atomic_write_file(path: Path):
    """Write a sibling temp file and replace the leaf after flush/fsync.

    ``open_file(path, 'write')`` intentionally holds the existing leaf. Replacing
    a leaf is a different operation, so ancestry is validated first and the new
    file is created exclusively in the same directory. This keeps a crash from
    leaving a half-truncated original while preserving the no-link guarantee.
    """
    with ExitStack() as stack:
        if os.name == "nt":
            # Every ancestor strictly above the leaf's own directory. Calling
            # open_parent(path) would also open the target leaf and fail for a
            # file that is about to be created.
            for parent in reversed(path.parent.parents):
                _check_windows_dir_no_delete(parent, stack)
        else:
            directory_flag = getattr(os, "O_DIRECTORY", None)
            nofollow = getattr(os, "O_NOFOLLOW", None)
            if directory_flag is None or nofollow is None:
                raise FileAccessDenied("This platform lacks secure no-follow directory opens")
            descriptor = os.open(path.anchor, os.O_RDONLY | directory_flag)
            stack.callback(os.close, descriptor)
            # path.parent is held by the root descriptor initially; open each
            # part below the filesystem root and finally the parent directory.
            for part in path.parent.parts[1:]:
                descriptor = os.open(part, os.O_RDONLY | directory_flag | nofollow,
                                     dir_fd=descriptor)
                stack.callback(os.close, descriptor)
        path.parent.mkdir(parents=True, exist_ok=True)
        candidate = path.with_name(f".{path.name}.nexus-{os.getpid()}-{os.urandom(4).hex()}.tmp")
        try:
            with open(candidate, "xb") as handle:
                yield handle
                handle.flush()
                os.fsync(handle.fileno())
            if candidate.is_symlink():
                raise FileAccessDenied("replacement became a link")
            os.replace(candidate, path)
        finally:
            try:
                candidate.unlink()
            except OSError:
                pass


def _check_windows_dir_no_delete(path: Path, stack: ExitStack) -> None:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(path), 0, 0x1 | 0x2, None, 3, 0x00200000 | 0x02000000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    stack.callback(close, handle)
    reject_link(path.lstat())
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise NotADirectoryError(str(path))


@contextmanager
def open_parent(path: Path):
    """Hold each verified ancestor while replacing a leaf in its directory."""
    with ExitStack() as stack:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            create = kernel.CreateFileW
            create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                               wintypes.HANDLE]
            create.restype = wintypes.HANDLE
            close = kernel.CloseHandle
            close.argtypes = [wintypes.HANDLE]
            close.restype = wintypes.BOOL
            for parent in reversed(path.parents):
                # Sharing read/write but not delete pins the validated ancestry.
                handle = create(str(parent), 0, 0x1 | 0x2, None, 3,
                                0x00200000 | 0x02000000, None)
                if handle == wintypes.HANDLE(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                stack.callback(close, handle)
                info = parent.lstat()
                reject_link(info)
                if not stat.S_ISDIR(info.st_mode):
                    raise NotADirectoryError(str(parent))
            yield None
            return

        directory_flag = getattr(os, "O_DIRECTORY", None)
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if directory_flag is None or nofollow is None:
            raise FileAccessDenied("This platform lacks secure no-follow directory opens")
        descriptor = os.open(path.anchor, os.O_RDONLY | directory_flag)
        stack.callback(os.close, descriptor)
        for part in path.parts[1:-1]:
            descriptor = os.open(part, os.O_RDONLY | directory_flag | nofollow,
                                 dir_fd=descriptor)
            stack.callback(os.close, descriptor)
        yield descriptor


@contextmanager
def open_child(parent_fd: int, path: Path, operation: str):
    """Open a leaf relative to a held parent directory without following links."""
    with ExitStack() as stack:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        nonblock = getattr(os, "O_NONBLOCK", None)
        if nofollow is None or nonblock is None:
            raise FileAccessDenied("This platform lacks secure no-follow file opens")
        flags = os.O_RDONLY if operation == "read" else os.O_WRONLY
        if operation == "create":
            flags |= os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(path.name, flags | nofollow | nonblock, 0o600,
                         dir_fd=parent_fd)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise FileAccessDenied("Linked or non-directory path component") from exc
            raise
        stack.callback(os.close, fd)
        regular_file(os.fstat(fd))
        if operation == "read":
            with os.fdopen(fd, "rb", closefd=False) as stream:
                yield stream
        else:
            # "wb" on an already-open descriptor does NOT truncate; it only sets
            # the mode. Writing a shorter file would leave the old tail behind,
            # which reads as silent corruption. Truncate the held handle and
            # rewind, because the handle keeps whatever position it had.
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            with os.fdopen(fd, "wb", closefd=False) as stream:
                yield stream


def _posix_open(path: Path, operation: str, stack: ExitStack) -> int:
    # These constants do not exist in Windows' os type stubs or runtime.
    directory_flag = getattr(os, "O_DIRECTORY", None)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if directory_flag is None or nofollow is None or nonblock is None:
        raise FileAccessDenied("This platform lacks secure no-follow file opens")
    parent = os.open(path.anchor, os.O_RDONLY | directory_flag)
    stack.callback(os.close, parent)
    try:
        for part in path.parts[1:-1]:
            parent = os.open(part, os.O_RDONLY | directory_flag | nofollow, dir_fd=parent)
            stack.callback(os.close, parent)
        flags = os.O_RDONLY if operation == "read" else os.O_WRONLY
        if operation == "create":
            flags |= os.O_CREAT | os.O_EXCL
        fd = os.open(path.name, flags | nofollow | nonblock, 0o600, dir_fd=parent)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise FileAccessDenied("Linked or non-directory path component") from exc
        raise
    stack.callback(os.close, fd)
    return fd


def _windows_open(path: Path, operation: str, stack: ExitStack) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL

    def handle_for(target: Path, access: int, disposition: int):
        # OPEN_REPARSE_POINT | BACKUP_SEMANTICS; READ/WRITE sharing, never DELETE.
        handle = create(str(target), access, 0x1 | 0x2, None, disposition,
                        0x00200000 | 0x02000000, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    for parent in reversed(path.parents):
        handle = handle_for(parent, 0, 3)  # OPEN_EXISTING, metadata only
        stack.callback(close, handle)
        info = parent.lstat()
        reject_link(info)
        if not stat.S_ISDIR(info.st_mode):
            raise NotADirectoryError(str(parent))
    access = 0x80000000 if operation == "read" else 0x40000000
    handle = handle_for(path, access, 1 if operation == "create" else 3)
    try:
        # Inspect the held leaf before converting its handle or touching contents.
        regular_file(path.lstat())
        flags = os.O_RDONLY if operation == "read" else os.O_WRONLY
        fd = msvcrt.open_osfhandle(handle, flags | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    stack.callback(os.close, fd)
    return fd
