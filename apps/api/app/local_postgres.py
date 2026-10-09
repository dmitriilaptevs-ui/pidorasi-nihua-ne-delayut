"""Start the bundled PostgreSQL server for local development and tests."""

from __future__ import annotations

import ctypes
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def start_postgres(data_dir: str | Path) -> Any:
    """Return a persistent pgserver instance using the given data directory."""
    import pgserver

    pgdata = Path(data_dir).resolve()
    pgdata.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        return pgserver.get_server(pgdata, cleanup_mode=None)

    import pgserver._commands as commands
    import pgserver.postgres_server as postgres_server

    postgres_bin = _windows_short_path(Path(commands.POSTGRES_BIN_PATH).resolve())
    pgdata = _windows_short_path(pgdata)
    temp_dir = _windows_short_path(Path(tempfile.gettempdir()).resolve())

    # pgserver's bundled Windows build inherits the machine's legacy code page.
    # Short ASCII paths keep initdb and pg_ctl from passing CP1251 paths to UTF-8
    # PostgreSQL. The ASCII role also avoids the Windows account name here.
    commands.POSTGRES_BIN_PATH = postgres_bin
    postgres_server.POSTGRES_BIN_PATH = postgres_bin

    if not (pgdata / "PG_VERSION").exists():
        environment = os.environ.copy()
        environment["TEMP"] = str(temp_dir)
        environment["TMP"] = str(temp_dir)
        initdb = postgres_bin / "initdb.exe"
        subprocess.run(
            [
                str(initdb),
                "-D",
                str(pgdata),
                "--locale=C",
                "--encoding=UTF8",
                "--auth=trust",
                "--auth-local=trust",
                "-U",
                "postgres",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )

    original_tempdir = tempfile.tempdir
    tempfile.tempdir = str(temp_dir)
    try:
        return pgserver.get_server(pgdata, cleanup_mode=None)
    finally:
        tempfile.tempdir = original_tempdir


def _windows_short_path(path: Path) -> Path:
    buffer = ctypes.create_unicode_buffer(32768)
    get_short_path_name = ctypes.windll.kernel32.GetShortPathNameW
    get_short_path_name.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    get_short_path_name.restype = ctypes.c_uint32
    length = get_short_path_name(str(path), buffer, len(buffer))
    if length == 0 or length >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())

    short_path = Path(buffer.value)
    if not str(short_path).isascii():
        raise RuntimeError(f"Windows did not provide an ASCII short path for {path}")
    return short_path
