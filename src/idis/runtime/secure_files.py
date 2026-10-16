"""Small standard-library secure-file primitives for strict bootstrap material.

The strict profile is loaded before third-party imports, so this module deliberately
uses only the Python standard library.  On Windows it reads through one non-sharing
handle and validates the owner/protected DACL from that handle.  On POSIX it uses
``O_NOFOLLOW`` and validates the opened inode and its parent directory.
"""

from __future__ import annotations

import ctypes
import os
import stat
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any, NoReturn, cast


class SecureFileError(RuntimeError):
    """An integrity-sensitive file could not be authenticated and read."""


if os.name == "nt":  # pragma: win32 cover
    from ctypes import wintypes

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    class _ACL(ctypes.Structure):
        _fields_ = [
            ("AclRevision", wintypes.BYTE),
            ("Sbz1", wintypes.BYTE),
            ("AclSize", wintypes.WORD),
            ("AceCount", wintypes.WORD),
            ("Sbz2", wintypes.WORD),
        ]

    class _ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("AceType", wintypes.BYTE),
            ("AceFlags", wintypes.BYTE),
            ("AceSize", wintypes.WORD),
        ]

    class _ACCESS_ALLOWED_ACE(ctypes.Structure):
        _fields_ = [
            ("Header", _ACE_HEADER),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        ]

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class _TOKEN_USER(ctypes.Structure):
        _fields_ = [("User", _SID_AND_ATTRIBUTES)]


_GENERIC_READ = 0x80000000
_READ_CONTROL = 0x00020000
_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_SHARE_READ = 0x00000001
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_READONLY = 0x00000001
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_TYPE_DISK = 0x0001
_INVALID_HANDLE_VALUE = int(ctypes.c_void_p(-1).value or -1)
_OWNER_SECURITY_INFORMATION = 0x00000001
_DACL_SECURITY_INFORMATION = 0x00000004
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_SE_FILE_OBJECT = 1
_SE_DACL_PROTECTED = 0x1000
_ACCESS_ALLOWED_ACE_TYPE = 0x00
_INHERITED_ACE = 0x10
_TOKEN_QUERY = 0x0008
_TOKEN_USER_CLASS = 1


def _raise_secure_file_error() -> NoReturn:
    raise SecureFileError("SECURE_FILE_INVALID")


def _windows_libraries() -> tuple[Any, Any, Any]:
    try:
        return (
            ctypes.WinDLL("kernel32", use_last_error=True),
            ctypes.WinDLL("advapi32", use_last_error=True),
            ctypes.WinDLL("user32", use_last_error=True),
        )
    except (AttributeError, OSError) as exc:  # pragma: no cover - platform guard
        raise SecureFileError("SECURE_FILE_INVALID") from exc


def _windows_close(kernel32: Any, handle: int | None) -> None:
    if handle not in (None, _INVALID_HANDLE_VALUE):
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def _windows_current_sid_bytes() -> bytes:
    kernel32, advapi32, _user32 = _windows_libraries()
    token = ctypes.c_void_p()
    advapi32.OpenProcessToken.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.OpenProcessToken.restype = ctypes.c_int
    advapi32.GetTokenInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    advapi32.GetTokenInformation.restype = ctypes.c_int
    advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
    advapi32.GetLengthSid.restype = ctypes.c_uint32
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
    ):
        _raise_secure_file_error()
    try:
        required = ctypes.c_uint32()
        advapi32.GetTokenInformation(token, _TOKEN_USER_CLASS, None, 0, ctypes.byref(required))
        if required.value == 0:
            _raise_secure_file_error()
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(
            token,
            _TOKEN_USER_CLASS,
            buffer,
            required.value,
            ctypes.byref(required),
        ):
            _raise_secure_file_error()
        sid = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_USER)).contents.User.Sid
        length = int(advapi32.GetLengthSid(sid))
        if length <= 0:
            _raise_secure_file_error()
        return ctypes.string_at(sid, length)
    finally:
        _windows_close(kernel32, int(token.value) if token.value is not None else None)


def _windows_sid_string(sid_bytes: bytes) -> str:
    _kernel32, advapi32, _user32 = _windows_libraries()
    sid_buffer = ctypes.create_string_buffer(sid_bytes)
    output = ctypes.c_wchar_p()
    if not advapi32.ConvertSidToStringSidW(sid_buffer, ctypes.byref(output)):
        _raise_secure_file_error()
    try:
        value = output.value
        if not value:
            _raise_secure_file_error()
        return value
    finally:
        ctypes.windll.kernel32.LocalFree(output)


def _windows_open(path: Path, *, directory: bool) -> int:
    kernel32, _advapi32, _user32 = _windows_libraries()
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    desired = _READ_CONTROL | _FILE_READ_ATTRIBUTES
    flags = _FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= _FILE_FLAG_BACKUP_SEMANTICS
    else:
        desired |= _GENERIC_READ
    handle = kernel32.CreateFileW(
        str(path),
        desired,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        flags,
        None,
    )
    value = int(handle) if handle is not None else _INVALID_HANDLE_VALUE
    if value == _INVALID_HANDLE_VALUE:
        _raise_secure_file_error()
    return value


def _windows_information(handle: int) -> Any:
    kernel32, _advapi32, _user32 = _windows_libraries()
    information = _BY_HANDLE_FILE_INFORMATION()
    if not kernel32.GetFileInformationByHandle(ctypes.c_void_p(handle), ctypes.byref(information)):
        _raise_secure_file_error()
    if kernel32.GetFileType(ctypes.c_void_p(handle)) != _FILE_TYPE_DISK:
        _raise_secure_file_error()
    return information


def _windows_identity(information: Any) -> tuple[int, int, int]:
    return (
        int(information.dwVolumeSerialNumber),
        int(information.nFileIndexHigh),
        int(information.nFileIndexLow),
    )


def _windows_acl_is_current_owner_only(handle: int, *, directory: bool) -> bool:
    kernel32, advapi32, _user32 = _windows_libraries()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    status = advapi32.GetSecurityInfo(
        ctypes.c_void_p(handle),
        _SE_FILE_OBJECT,
        _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if status != 0 or not descriptor.value or not owner.value or not dacl.value:
        if descriptor.value:
            kernel32.LocalFree(descriptor)
        return False
    try:
        current_sid = _windows_current_sid_bytes()
        owner_length = int(advapi32.GetLengthSid(owner))
        if owner_length <= 0 or ctypes.string_at(owner, owner_length) != current_sid:
            return False
        control = ctypes.c_uint16()
        revision = ctypes.c_uint32()
        if (
            not advapi32.GetSecurityDescriptorControl(
                descriptor, ctypes.byref(control), ctypes.byref(revision)
            )
            or not control.value & _SE_DACL_PROTECTED
        ):
            return False
        acl = ctypes.cast(dacl, ctypes.POINTER(_ACL)).contents
        if acl.AceCount != 1:
            return False
        ace_pointer = ctypes.c_void_p()
        if not advapi32.GetAce(dacl, 0, ctypes.byref(ace_pointer)) or not ace_pointer.value:
            return False
        ace = ctypes.cast(ace_pointer, ctypes.POINTER(_ACCESS_ALLOWED_ACE)).contents
        allowed_flags = 0x03 if directory else 0x00  # object/container inheritance on directories
        if (
            ace.Header.AceType != _ACCESS_ALLOWED_ACE_TYPE
            or ace.Header.AceFlags & _INHERITED_ACE
            or ace.Header.AceFlags & ~allowed_flags
        ):
            return False
        sid_address = int(ace_pointer.value) + _ACCESS_ALLOWED_ACE.SidStart.offset
        sid_pointer = ctypes.c_void_p(sid_address)
        sid_length = int(advapi32.GetLengthSid(sid_pointer))
        return sid_length > 0 and ctypes.string_at(sid_pointer, sid_length) == current_sid
    finally:
        kernel32.LocalFree(descriptor)


def protect_path_for_current_user(
    path: Path, *, directory: bool = False, verify: bool = True
) -> None:
    """Apply one protected current-user full-control DACL to a file/directory."""

    target = Path(path)
    if os.name != "nt":
        mode = stat.S_IRUSR | stat.S_IWUSR | (stat.S_IXUSR if directory else 0)
        target.chmod(mode)
        return
    kernel32, advapi32, _user32 = _windows_libraries()
    sid = _windows_sid_string(_windows_current_sid_bytes())
    inheritance = "OICI" if directory else ""
    sddl = f"D:P(A;{inheritance};FA;;;{sid})"
    descriptor = ctypes.c_void_p()
    descriptor_size = ctypes.c_uint32()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        1,
        ctypes.byref(descriptor),
        ctypes.byref(descriptor_size),
    ):
        _raise_secure_file_error()
    try:
        present = ctypes.c_int()
        defaulted = ctypes.c_int()
        dacl = ctypes.c_void_p()
        if (
            not advapi32.GetSecurityDescriptorDacl(
                descriptor,
                ctypes.byref(present),
                ctypes.byref(dacl),
                ctypes.byref(defaulted),
            )
            or not present.value
            or not dacl.value
        ):
            _raise_secure_file_error()
        status = advapi32.SetNamedSecurityInfoW(
            ctypes.c_wchar_p(str(target)),
            _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            dacl,
            None,
        )
        if status != 0:
            _raise_secure_file_error()
    finally:
        kernel32.LocalFree(descriptor)
    if verify and not path_acl_is_current_owner_only(target, directory=directory):
        _raise_secure_file_error()


def path_acl_is_current_owner_only(path: Path, *, directory: bool = False) -> bool:
    """Inspect owner/protected DACL through a non-sharing handle."""

    if os.name != "nt":
        try:
            metadata = os.stat(path, follow_symlinks=False)
            geteuid = getattr(os, "geteuid", None)
            expected = stat.S_IRUSR | stat.S_IWUSR | (stat.S_IXUSR if directory else 0)
            return bool(
                callable(geteuid)
                and metadata.st_uid == geteuid()
                and stat.S_IMODE(metadata.st_mode) == expected
            )
        except OSError:
            return False
    handle: int | None = None
    try:
        handle = _windows_open(Path(path), directory=directory)
        info = _windows_information(handle)
        attributes = int(info.dwFileAttributes)
        if bool(attributes & _FILE_ATTRIBUTE_DIRECTORY) != directory:
            return False
        if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            return False
        return _windows_acl_is_current_owner_only(handle, directory=directory)
    except SecureFileError:
        return False
    finally:
        if handle is not None:
            kernel32, _advapi32, _user32 = _windows_libraries()
            _windows_close(kernel32, handle)


def _windows_read(
    path: Path,
    *,
    max_bytes: int,
    require_readonly: bool,
    require_secure_parent: bool,
) -> bytes:
    kernel32, _advapi32, _user32 = _windows_libraries()
    parent_handle: int | None = None
    file_handle: int | None = None
    try:
        if require_secure_parent:
            parent_handle = _windows_open(path.parent, directory=True)
            parent_info = _windows_information(parent_handle)
            parent_attributes = int(parent_info.dwFileAttributes)
            if (
                not parent_attributes & _FILE_ATTRIBUTE_DIRECTORY
                or parent_attributes & _FILE_ATTRIBUTE_REPARSE_POINT
                or not _windows_acl_is_current_owner_only(parent_handle, directory=True)
            ):
                _raise_secure_file_error()
        file_handle = _windows_open(path, directory=False)
        before = _windows_information(file_handle)
        attributes = int(before.dwFileAttributes)
        size = (int(before.nFileSizeHigh) << 32) | int(before.nFileSizeLow)
        if (
            attributes & (_FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_REPARSE_POINT)
            or require_readonly
            and not attributes & _FILE_ATTRIBUTE_READONLY
            or size <= 0
            or size > max_bytes
            or not _windows_acl_is_current_owner_only(file_handle, directory=False)
        ):
            _raise_secure_file_error()
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            buffer = ctypes.create_string_buffer(min(4096, remaining))
            read = ctypes.c_uint32()
            if not kernel32.ReadFile(
                ctypes.c_void_p(file_handle),
                buffer,
                len(buffer),
                ctypes.byref(read),
                None,
            ):
                _raise_secure_file_error()
            if read.value == 0:
                break
            chunks.append(buffer.raw[: read.value])
            remaining -= int(read.value)
        after = _windows_information(file_handle)
        data = b"".join(chunks)
        if (
            not data
            or len(data) > max_bytes
            or _windows_identity(before) != _windows_identity(after)
            or int(after.dwFileAttributes) & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            _raise_secure_file_error()
        return data
    finally:
        _windows_close(kernel32, file_handle)
        _windows_close(kernel32, parent_handle)


def _posix_read(
    path: Path,
    *,
    max_bytes: int,
    require_readonly: bool,
    require_secure_parent: bool,
    require_owner_only: bool,
) -> bytes:
    geteuid = getattr(os, "geteuid", None)
    if not callable(geteuid):
        _raise_secure_file_error()
    expected_uid = cast(Callable[[], int], geteuid)()
    parent_descriptor: int | None = None
    descriptor: int | None = None
    try:
        if require_secure_parent:
            parent_flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            parent_descriptor = os.open(path.parent, parent_flags)
            parent = os.fstat(parent_descriptor)
            if (
                not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid != expected_uid
                or stat.S_IMODE(parent.st_mode) & (stat.S_IWGRP | stat.S_IWOTH)
            ):
                _raise_secure_file_error()
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != expected_uid
            or require_owner_only
            and stat.S_IMODE(opened.st_mode) & (stat.S_IRWXG | stat.S_IRWXO)
            or require_readonly
            and stat.S_IMODE(opened.st_mode) & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
            or opened.st_size <= 0
            or opened.st_size > max_bytes
        ):
            _raise_secure_file_error()
        path_metadata = os.lstat(path)
        identity = (opened.st_dev, opened.st_ino)
        if (
            stat.S_ISLNK(path_metadata.st_mode)
            or (
                path_metadata.st_dev,
                path_metadata.st_ino,
            )
            != identity
        ):
            _raise_secure_file_error()
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(4096, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        final_path = os.lstat(path)
        data = b"".join(chunks)
        if (
            not data
            or len(data) > max_bytes
            or (after.st_dev, after.st_ino) != identity
            or stat.S_ISLNK(final_path.st_mode)
            or (final_path.st_dev, final_path.st_ino) != identity
        ):
            _raise_secure_file_error()
        return data
    except OSError as exc:
        raise SecureFileError("SECURE_FILE_INVALID") from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if parent_descriptor is not None:
            with suppress(OSError):
                os.close(parent_descriptor)


def secure_read_integrity_file(
    path_value: str,
    *,
    max_bytes: int,
    require_readonly: bool = True,
    require_secure_parent: bool = True,
    require_owner_only: bool = False,
) -> bytes:
    """Read an absolute regular file while proving its integrity boundary."""

    path = Path(path_value)
    if max_bytes <= 0 or not path.is_absolute():
        _raise_secure_file_error()
    try:
        if os.name == "nt":
            return _windows_read(
                path,
                max_bytes=max_bytes,
                require_readonly=require_readonly,
                require_secure_parent=require_secure_parent,
            )
        return _posix_read(
            path,
            max_bytes=max_bytes,
            require_readonly=require_readonly,
            require_secure_parent=require_secure_parent,
            require_owner_only=require_owner_only,
        )
    except SecureFileError:
        raise
    except (OSError, ValueError) as exc:
        raise SecureFileError("SECURE_FILE_INVALID") from exc


__all__ = [
    "SecureFileError",
    "path_acl_is_current_owner_only",
    "protect_path_for_current_user",
    "secure_read_integrity_file",
]
