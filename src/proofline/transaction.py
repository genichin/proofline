"""POSIX transaction primitives shared by project artifact writers."""

from __future__ import annotations

import ctypes
import errno
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

AT_FDCWD = -100
RENAME_NOREPLACE = 1
RENAME_EXCHANGE = 2


@dataclass(frozen=True)
class FileSnapshot:
    data: bytes
    identity: tuple[int, int]


@dataclass(frozen=True)
class DirectoryPin:
    path: Path
    descriptor: int
    identity: tuple[int, int]

    def close(self) -> None:
        os.close(self.descriptor)


@dataclass(frozen=True)
class TreeSnapshot:
    identity: tuple[int, int]
    files: dict[str, tuple[tuple[int, int], bytes]]
    directories: dict[str, tuple[int, int]]


def identity(state: os.stat_result) -> tuple[int, int]:
    return state.st_dev, state.st_ino


def read_regular(path: Path) -> FileSnapshot:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        state = os.fstat(descriptor)
        if not stat.S_ISREG(state.st_mode):
            raise OSError(errno.EINVAL, "not a regular file", path)
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            data = stream.read()
        current = path.stat(follow_symlinks=False)
        if identity(current) != identity(state) or not stat.S_ISREG(current.st_mode):
            raise OSError(errno.ESTALE, "path changed while reading", path)
        return FileSnapshot(data, identity(state))
    finally:
        os.close(descriptor)


def open_directory(path: Path) -> DirectoryPin:
    descriptor = os.open(
        path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    state = os.fstat(descriptor)
    return DirectoryPin(path, descriptor, identity(state))


def open_child_directory(parent: DirectoryPin, name: str, display: Path) -> DirectoryPin:
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=parent.descriptor,
    )
    state = os.fstat(descriptor)
    return DirectoryPin(display, descriptor, identity(state))


def verify_directory(pin: DirectoryPin) -> None:
    descriptor_state = os.fstat(pin.descriptor)
    pathname_state = pin.path.stat(follow_symlinks=False)
    if (
        identity(descriptor_state) != pin.identity
        or identity(pathname_state) != pin.identity
        or not stat.S_ISDIR(pathname_state.st_mode)
    ):
        raise OSError(errno.ESTALE, "directory changed after preflight", pin.path)


def verify_regular(path: Path, snapshot: FileSnapshot) -> None:
    current = read_regular(path)
    if current.identity != snapshot.identity or current.data != snapshot.data:
        raise OSError(errno.ESTALE, "file changed after preflight", path)


def _renameat2(
    source_fd: int,
    source_name: str | bytes,
    target_fd: int,
    target_name: str | bytes,
    flags: int,
) -> None:
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError) as exc:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable") from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if renameat2(source_fd, os.fsencode(source_name), target_fd, os.fsencode(target_name), flags):
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), target_name)


def _private_name(parent_fd: int) -> str:
    while True:
        name = f".proofline-quarantine-{os.getpid()}-{secrets.token_hex(8)}"
        try:
            os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return name


def _read_regular_at(parent_fd: int, name: str) -> FileSnapshot:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
    try:
        state = os.fstat(descriptor)
        if not stat.S_ISREG(state.st_mode):
            raise OSError(errno.ESTALE, "not an owned regular file", name)
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            data = stream.read()
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if identity(current) != identity(state) or not stat.S_ISREG(current.st_mode):
            raise OSError(errno.ESTALE, "file path changed", name)
        return FileSnapshot(data, identity(state))
    finally:
        os.close(descriptor)


def read_regular_at(parent_fd: int, name: str) -> FileSnapshot:
    return _read_regular_at(parent_fd, name)


def _has_directory_fd_primitives() -> bool:
    return (
        all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC"))
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
    )


def _is_reparse_point(state: os.stat_result) -> bool:
    attributes = getattr(state, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(state.st_mode) or bool(attributes & reparse_flag)


def _read_regular_beneath_by_path(root: Path, parts: tuple[str, ...]) -> FileSnapshot:
    directory_states: list[tuple[Path, tuple[int, int]]] = []
    current = root
    for part in parts[:-1]:
        state = current.stat(follow_symlinks=False)
        if _is_reparse_point(state) or not stat.S_ISDIR(state.st_mode):
            raise OSError(errno.ELOOP, "unsafe directory component", current)
        directory_states.append((current, identity(state)))
        current /= part

    parent_state = current.stat(follow_symlinks=False)
    if _is_reparse_point(parent_state) or not stat.S_ISDIR(parent_state.st_mode):
        raise OSError(errno.ELOOP, "unsafe directory component", current)
    directory_states.append((current, identity(parent_state)))

    target = current / parts[-1]
    before = target.stat(follow_symlinks=False)
    if _is_reparse_point(before) or not stat.S_ISREG(before.st_mode):
        raise OSError(errno.EINVAL, "not a regular file", target)
    with target.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or identity(opened) != identity(before):
            raise OSError(errno.ESTALE, "path changed while opening", target)
        data = stream.read()

    after = target.stat(follow_symlinks=False)
    if (
        _is_reparse_point(after)
        or not stat.S_ISREG(after.st_mode)
        or identity(after) != identity(opened)
    ):
        raise OSError(errno.ESTALE, "path changed while reading", target)
    for directory, expected in directory_states:
        current_state = directory.stat(follow_symlinks=False)
        if (
            _is_reparse_point(current_state)
            or not stat.S_ISDIR(current_state.st_mode)
            or identity(current_state) != expected
        ):
            raise OSError(errno.ESTALE, "directory changed while reading", directory)
    return FileSnapshot(data, identity(opened))


def read_regular_beneath(root: Path, relative: str) -> FileSnapshot:
    relative_path = Path(relative)
    parts = relative_path.parts
    if (
        not parts
        or relative_path.is_absolute()
        or relative_path.drive
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise OSError(errno.EINVAL, "invalid relative path", relative)
    if not _has_directory_fd_primitives():
        return _read_regular_beneath_by_path(root, parts)
    pins = [open_directory(root)]
    try:
        for index, part in enumerate(parts[:-1]):
            pins.append(
                open_child_directory(
                    pins[-1], part, root.joinpath(*parts[: index + 1])
                )
            )
        snapshot = _read_regular_at(pins[-1].descriptor, parts[-1])
        for pin in pins:
            verify_directory(pin)
        return snapshot
    finally:
        for pin in reversed(pins):
            pin.close()


def _inspect_tree_fd(descriptor: int, root_identity: tuple[int, int]) -> TreeSnapshot:
    files: dict[str, tuple[tuple[int, int], bytes]] = {}
    directories: dict[str, tuple[int, int]] = {}

    def inspect(directory_fd: int, prefix: str) -> None:
        for entry in os.listdir(directory_fd):
            relative = f"{prefix}/{entry}" if prefix else entry
            state = os.stat(entry, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISREG(state.st_mode):
                snapshot = _read_regular_at(directory_fd, entry)
                files[relative] = (snapshot.identity, snapshot.data)
            elif stat.S_ISDIR(state.st_mode):
                child = os.open(
                    entry,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=directory_fd,
                )
                try:
                    opened = os.fstat(child)
                    if identity(opened) != identity(state):
                        raise OSError(errno.ESTALE, "tree directory changed", relative)
                    directories[relative] = identity(opened)
                    inspect(child, relative)
                finally:
                    os.close(child)
            else:
                raise OSError(errno.ESTALE, "unsupported tree entry", relative)

    inspect(descriptor, "")
    return TreeSnapshot(root_identity, files, directories)


def snapshot_tree(path: Path) -> TreeSnapshot:
    descriptor = os.open(
        path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        state = os.fstat(descriptor)
        snapshot = _inspect_tree_fd(descriptor, identity(state))
        current = path.stat(follow_symlinks=False)
        if identity(current) != snapshot.identity or not stat.S_ISDIR(current.st_mode):
            raise OSError(errno.ESTALE, "tree path changed", path)
        return snapshot
    finally:
        os.close(descriptor)


def _snapshot_at(parent_fd: int, name: str) -> FileSnapshot | TreeSnapshot:
    state = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if stat.S_ISREG(state.st_mode):
        return _read_regular_at(parent_fd, name)
    if not stat.S_ISDIR(state.st_mode):
        raise OSError(errno.ESTALE, "unsupported committed object", name)
    descriptor = os.open(
        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=parent_fd,
    )
    try:
        opened = os.fstat(descriptor)
        if identity(opened) != identity(state):
            raise OSError(errno.ESTALE, "committed tree changed", name)
        return _inspect_tree_fd(descriptor, identity(opened))
    finally:
        os.close(descriptor)


def commit_no_replace(
    source: Path,
    target_dir_fd: int,
    target_name: str,
    expected: FileSnapshot | TreeSnapshot | None = None,
) -> tuple[int, int]:
    if expected is None:
        state = source.stat(follow_symlinks=False)
        expected = read_regular(source) if stat.S_ISREG(state.st_mode) else snapshot_tree(source)
    source_parent = os.open(
        source.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        _renameat2(source_parent, source.name, target_dir_fd, target_name, RENAME_NOREPLACE)
    finally:
        os.close(source_parent)
    try:
        committed = _snapshot_at(target_dir_fd, target_name)
        if committed != expected:
            raise OSError(errno.ESTALE, "committed source ownership changed", target_name)
    except OSError:
        quarantine = _private_name(target_dir_fd)
        try:
            _renameat2(target_dir_fd, target_name, target_dir_fd, quarantine, RENAME_NOREPLACE)
        except OSError:
            pass
        raise
    return committed.identity


def exchange_owned_file(
    parent_fd: int,
    canonical_name: str,
    candidate_name: str,
    expected_current: FileSnapshot,
    expected_candidate: FileSnapshot,
) -> tuple[int, int]:
    _renameat2(parent_fd, candidate_name, parent_fd, canonical_name, RENAME_EXCHANGE)
    try:
        displaced = _read_regular_at(parent_fd, candidate_name)
        committed = _read_regular_at(parent_fd, canonical_name)
        if displaced != expected_current or committed != expected_candidate:
            raise OSError(errno.ESTALE, "exchange ownership changed", canonical_name)
    except OSError:
        _renameat2(parent_fd, candidate_name, parent_fd, canonical_name, RENAME_EXCHANGE)
        raise
    return committed.identity


def _move_placeholder_private(
    parent_fd: int, canonical_name: str, placeholder_identity: tuple[int, int], directory: bool
) -> str:
    private = _private_name(parent_fd)
    _renameat2(parent_fd, canonical_name, parent_fd, private, RENAME_NOREPLACE)
    state = os.stat(private, dir_fd=parent_fd, follow_symlinks=False)
    valid_type = stat.S_ISDIR(state.st_mode) if directory else stat.S_ISREG(state.st_mode)
    if identity(state) != placeholder_identity or not valid_type:
        _renameat2(parent_fd, private, parent_fd, canonical_name, RENAME_NOREPLACE)
        raise OSError(errno.ESTALE, "canonical placeholder changed", canonical_name)
    return private


def remove_owned_file(
    parent_fd: int, name: str, expected_identity: tuple[int, int], expected: bytes
) -> None:
    quarantine = _private_name(parent_fd)
    placeholder_fd = os.open(
        quarantine,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=parent_fd,
    )
    placeholder_identity = identity(os.fstat(placeholder_fd))
    os.close(placeholder_fd)
    try:
        _renameat2(parent_fd, quarantine, parent_fd, name, RENAME_EXCHANGE)
        displaced = _read_regular_at(parent_fd, quarantine)
        if displaced.identity != expected_identity or displaced.data != expected:
            _renameat2(parent_fd, quarantine, parent_fd, name, RENAME_EXCHANGE)
            raise OSError(errno.ESTALE, "file ownership changed", name)
        os.unlink(quarantine, dir_fd=parent_fd)
        private_placeholder = _move_placeholder_private(
            parent_fd, name, placeholder_identity, False
        )
        os.unlink(private_placeholder, dir_fd=parent_fd)
    except Exception:
        try:
            state = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
            if identity(state) == placeholder_identity:
                os.unlink(quarantine, dir_fd=parent_fd)
        except OSError:
            pass
        raise


def _restore_missing_tree_entries(
    descriptor: int,
    expected_files: dict[str, tuple[tuple[int, int], bytes]],
    expected_directories: dict[str, tuple[int, int]],
) -> None:
    safe_directories: dict[str, tuple[int, int]] = {
        "": identity(os.fstat(descriptor))
    }
    created_directory_fds: list[int] = []

    def open_safe(relative: str) -> tuple[int, list[int]]:
        current_fd = descriptor
        opened: list[int] = []
        prefix = ""
        try:
            for part in relative.split("/") if relative else ():
                prefix = f"{prefix}/{part}" if prefix else part
                expected = safe_directories.get(prefix)
                if expected is None:
                    raise OSError(errno.ESTALE, "unsafe restore directory", prefix)
                child = os.open(
                    part,
                    os.O_RDONLY
                    | os.O_DIRECTORY
                    | os.O_NOFOLLOW
                    | os.O_CLOEXEC,
                    dir_fd=current_fd,
                )
                if identity(os.fstat(child)) != expected:
                    os.close(child)
                    raise OSError(errno.ESTALE, "restore directory changed", prefix)
                opened.append(child)
                current_fd = child
            return current_fd, opened
        except Exception:
            for item in reversed(opened):
                os.close(item)
            raise

    for relative in sorted(
        expected_directories, key=lambda value: value.count("/")
    ):
        parent, _, name = relative.rpartition("/")
        opened: list[int] = []
        child: int | None = None
        try:
            parent_fd, opened = open_safe(parent)
            try:
                child = os.open(
                    name,
                    os.O_RDONLY
                    | os.O_DIRECTORY
                    | os.O_NOFOLLOW
                    | os.O_CLOEXEC,
                    dir_fd=parent_fd,
                )
                child_identity = identity(os.fstat(child))
                if child_identity != expected_directories[relative]:
                    continue
            except FileNotFoundError:
                private = _private_name(parent_fd)
                os.mkdir(private, 0o700, dir_fd=parent_fd)
                child = os.open(
                    private,
                    os.O_RDONLY
                    | os.O_DIRECTORY
                    | os.O_NOFOLLOW
                    | os.O_CLOEXEC,
                    dir_fd=parent_fd,
                )
                child_identity = identity(os.fstat(child))
                try:
                    _renameat2(
                        parent_fd,
                        private,
                        parent_fd,
                        name,
                        RENAME_NOREPLACE,
                    )
                except OSError:
                    created_directory_fds.append(child)
                    child = None
                    continue
                current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if identity(current) != child_identity:
                    created_directory_fds.append(child)
                    child = None
                    continue
                created_directory_fds.append(child)
                child = None
            safe_directories[relative] = child_identity
        except OSError:
            pass
        finally:
            if child is not None:
                os.close(child)
            for item in reversed(opened):
                os.close(item)

    for relative, (_, data) in expected_files.items():
        parent, _, name = relative.rpartition("/")
        opened: list[int] = []
        file_fd: int | None = None
        try:
            parent_fd, opened = open_safe(parent)
            try:
                file_fd = os.open(
                    name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | os.O_NOFOLLOW
                    | os.O_CLOEXEC,
                    0o600,
                    dir_fd=parent_fd,
                )
            except FileExistsError:
                continue
            with os.fdopen(os.dup(file_fd), "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            pass
        finally:
            if file_fd is not None:
                os.close(file_fd)
            for item in reversed(opened):
                os.close(item)

    for item in reversed(created_directory_fds):
        os.close(item)


def remove_owned_tree(
    parent_fd: int,
    name: str,
    expected_identity: tuple[int, int],
    expected_files: dict[str, tuple[tuple[int, int], bytes]],
    expected_directories: dict[str, tuple[int, int]] | None = None,
) -> None:
    quarantine = _private_name(parent_fd)
    os.mkdir(quarantine, 0o700, dir_fd=parent_fd)
    placeholder_fd = os.open(
        quarantine,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=parent_fd,
    )
    placeholder = os.fstat(placeholder_fd)
    placeholder_identity = identity(placeholder)
    try:
        _renameat2(parent_fd, quarantine, parent_fd, name, RENAME_EXCHANGE)
        descriptor = os.open(
            quarantine,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except Exception:
        os.close(placeholder_fd)
        raise
    directory_identities = expected_directories or {}
    expected_directory_names = set(directory_identities)
    owned_directory_fds: list[int] = []

    def close_owned_directories() -> None:
        while owned_directory_fds:
            os.close(owned_directory_fds.pop())

    try:
        state = os.fstat(descriptor)
        if identity(state) != expected_identity:
            raise OSError(errno.ESTALE, "directory ownership changed", name)
        actual_files: set[str] = set()
        actual_directories: set[str] = set()

        def inspect(directory_fd: int, prefix: str) -> None:
            for entry in os.listdir(directory_fd):
                relative = f"{prefix}/{entry}" if prefix else entry
                child_state = os.stat(entry, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISREG(child_state.st_mode):
                    expected = expected_files.get(relative)
                    if expected is None:
                        raise OSError(errno.ESTALE, "unexpected file", relative)
                    child = os.open(
                        entry,
                        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                        dir_fd=directory_fd,
                    )
                    try:
                        opened = os.fstat(child)
                        with os.fdopen(os.dup(child), "rb") as stream:
                            data = stream.read()
                        if identity(opened) != expected[0] or data != expected[1]:
                            raise OSError(errno.ESTALE, "file ownership changed", relative)
                    finally:
                        os.close(child)
                    actual_files.add(relative)
                elif stat.S_ISDIR(child_state.st_mode):
                    if relative not in expected_directory_names:
                        raise OSError(errno.ESTALE, "unexpected directory", relative)
                    expected_identity_value = directory_identities.get(relative)
                    if expected_identity_value is not None and identity(child_state) != expected_identity_value:
                        raise OSError(errno.ESTALE, "directory ownership changed", relative)
                    child = os.open(
                        entry,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                        dir_fd=directory_fd,
                    )
                    try:
                        inspect(child, relative)
                    except Exception:
                        os.close(child)
                        raise
                    owned_directory_fds.append(child)
                    actual_directories.add(relative)
                else:
                    raise OSError(errno.ESTALE, "unsupported tree entry", relative)

        inspect(descriptor, "")
        if actual_files != set(expected_files) or actual_directories != expected_directory_names:
            raise OSError(errno.ESTALE, "tree topology changed", name)

        for filename in sorted(relative for relative in expected_files if "/" not in relative):
            file_identity, data = expected_files[filename]
            remove_owned_file(descriptor, filename, file_identity, data)
        for directory_name in sorted(
            relative for relative in expected_directory_names if "/" not in relative
        ):
            prefix = f"{directory_name}/"
            child_files = {
                relative.removeprefix(prefix): value
                for relative, value in expected_files.items()
                if relative.startswith(prefix)
            }
            child_directories = {
                relative.removeprefix(prefix): value
                for relative, value in directory_identities.items()
                if relative.startswith(prefix)
            }
            remove_owned_tree(
                descriptor,
                directory_name,
                directory_identities[directory_name],
                child_files,
                child_directories,
            )
        close_owned_directories()
        os.close(descriptor)
        descriptor = -1
        os.rmdir(quarantine, dir_fd=parent_fd)
        private_placeholder = _move_placeholder_private(
            parent_fd, name, placeholder_identity, True
        )
        os.rmdir(private_placeholder, dir_fd=parent_fd)
    except Exception:
        if descriptor >= 0:
            if identity(os.fstat(descriptor)) == expected_identity:
                _restore_missing_tree_entries(
                    descriptor, expected_files, directory_identities
                )
            close_owned_directories()
            os.close(descriptor)
            descriptor = -1
        try:
            private = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
            if stat.S_ISDIR(private.st_mode):
                restore_identity = identity(private)
                private_placeholder = _move_placeholder_private(
                    parent_fd, name, placeholder_identity, True
                )
                try:
                    _renameat2(
                        parent_fd,
                        quarantine,
                        parent_fd,
                        name,
                        RENAME_NOREPLACE,
                    )
                    restored = os.stat(
                        name, dir_fd=parent_fd, follow_symlinks=False
                    )
                    if identity(restored) != restore_identity:
                        raise OSError(errno.ESTALE, "rollback commit changed")
                except OSError:
                    try:
                        _renameat2(
                            parent_fd,
                            private_placeholder,
                            parent_fd,
                            name,
                            RENAME_NOREPLACE,
                        )
                    except OSError:
                        pass
                    raise
                os.rmdir(private_placeholder, dir_fd=parent_fd)
        except OSError:
            pass
        raise
    finally:
        close_owned_directories()
        if descriptor >= 0:
            os.close(descriptor)
        os.close(placeholder_fd)
