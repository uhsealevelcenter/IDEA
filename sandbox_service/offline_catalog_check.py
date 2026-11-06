"""Startup-only catalog validation, before any native SDK/VM writer exists.

Never import this into the API or run it while the service is live. A separate
Python process alone is insufficient isolation from an active native catalog.
"""

import os
from pathlib import Path
import sqlite3


def assert_offline(proc_root=Path('/proc')):
    for process in proc_root.glob('[0-9]*'):
        if process.name == str(os.getpid()):
            continue
        try:
            arguments = (process / 'cmdline').read_bytes().split(b'\0')
        except FileNotFoundError:
            continue
        executables = {Path(os.fsdecode(arg)).name for arg in arguments if arg}
        if 'msb' in executables or any('uvicorn' in arg for arg in executables) or any(arg.startswith('python') for arg in executables):
            raise RuntimeError('Offline catalog check refused: another API/SDK/VM process is present')


def main():
    assert_offline()
    home = Path(os.environ.get('MSB_HOME') or Path.home() / '.microsandbox')
    database = home / 'db' / 'msb.db'
    if not database.is_file():
        print('No existing Microsandbox catalog; native SDK will initialize it')
        return
    connection = sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True)
    try:
        integrity = connection.execute('PRAGMA integrity_check').fetchall()
        foreign_keys = connection.execute('PRAGMA foreign_key_check').fetchall()
        if integrity != [('ok',)] or foreign_keys:
            raise RuntimeError(f'Microsandbox catalog failed offline validation: {integrity[:5]}, foreign keys: {foreign_keys[:5]}')
    finally:
        connection.close()
    print('Offline Microsandbox catalog integrity and foreign keys: OK')


if __name__ == '__main__':
    main()
