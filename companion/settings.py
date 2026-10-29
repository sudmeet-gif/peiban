"""Local app configuration and verified data directory migration."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import sys
from contextlib import closing
from pathlib import Path

from .storage import Store


def _digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


class Settings:
    def __init__(self, config_path=None):
        app_dir = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent
        legacy = app_dir / 'settings.json'
        user_dir = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'Peiban'
        default = legacy if not getattr(sys, 'frozen', False) or legacy.exists() else user_dir / 'settings.json'
        self.config_path = Path(config_path or default).expanduser().resolve()
        self._values = {}
        if self.config_path.exists():
            try:
                with self.config_path.open('r', encoding='utf-8') as f:
                    value = json.load(f)
                if not isinstance(value, dict):
                    raise ValueError('设置文件格式错误')
                self._values = value
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError('设置文件无法读取，请先备份并修复') from exc

    def get(self, key, default=None):
        return self._values.get(key, default)

    def set(self, key, value):
        values = dict(self._values)
        values[key] = value
        self._write(values)
        self._values = values

    def _write(self, values):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix='.settings-', suffix='.tmp', dir=str(self.config_path.parent))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(values, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, self.config_path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    @property
    def data_dir(self):
        return Path(self.get('data_dir', str(self.config_path.parent / 'data'))).expanduser().resolve()

    def use_existing_data(self, directory):
        """Switch to a verified existing data folder without modifying its contents."""
        target = Path(directory).expanduser().resolve()
        database = target / 'companion.sqlite3'
        if not database.is_file():
            raise ValueError('该文件夹没有陪伴软件的数据文件，请选择原来的数据目录。')
        try:
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'personas', 'messages', 'memories', 'sources'} <= tables:
                    raise ValueError('该文件夹不是有效的陪伴数据目录。')
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('数据文件校验失败。')
        except sqlite3.DatabaseError as exc:
            raise ValueError('无法读取该数据文件。') from exc
        self.set('data_dir', str(target))
        return target

    def migrate_data(self, new_dir):
        """Verify a copy and switch settings; caller must pause all background writers first.

        Original files are deliberately retained until the user removes them.
        """
        source = self.data_dir
        target = Path(new_dir).expanduser().resolve()
        if target == source:
            return Store(source)
        if target.is_relative_to(source) or source.is_relative_to(target):
            raise ValueError('新旧数据目录不能互相包含')
        if target.exists() and any(target.iterdir()):
            raise ValueError('新数据目录必须为空，避免覆盖已有文件')
        store = Store(source)
        # ZIP restore validates paths and SQLite integrity before publishing the new directory.
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / 'backup.zip'
            store.backup(archive)
            restored = Store.restore_backup(archive, target)
            source_media = source / 'attachments'
            target_media = target / 'attachments'
            if source_media.exists():
                for item in source_media.rglob('*'):
                    if item.is_file() and not item.is_symlink():
                        relative = item.relative_to(source_media)
                        dest = target_media / relative
                        if not dest.is_file() or _digest(item) != _digest(dest):
                            raise ValueError('文件复制校验失败；原目录未更改')
        self.set('data_dir', str(target))
        return restored

    def restore_backup(self, archive, new_dir):
        target = Path(new_dir).expanduser().resolve()
        current = self.data_dir
        if target == current or target.is_relative_to(current) or current.is_relative_to(target):
            raise ValueError('请选择与当前数据目录互不包含的空目录')
        restored = Store.restore_backup(archive, target)
        self.set('data_dir', str(target))
        return restored
