"""Local, per-character storage. No network access is performed here."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import closing, contextmanager
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS personas (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, avatar TEXT NOT NULL DEFAULT '',
 profile TEXT NOT NULL DEFAULT '', voice_mode TEXT NOT NULL DEFAULT 'none',
 voice_name TEXT NOT NULL DEFAULT 'Vivian', voice_ref TEXT NOT NULL DEFAULT '',
 voice_text TEXT NOT NULL DEFAULT '', auto_voice INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
 name TEXT NOT NULL, content_hash TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT '',
 copied_path TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'complete',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(persona_id, content_hash)
);
CREATE TABLE IF NOT EXISTS messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT, persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
 role TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', media_path TEXT NOT NULL DEFAULT '',
 media_type TEXT NOT NULL DEFAULT '', source_id TEXT REFERENCES sources(id) ON DELETE SET NULL,
 timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS messages_persona ON messages(persona_id, id);
CREATE TABLE IF NOT EXISTS memories (
 id INTEGER PRIMARY KEY AUTOINCREMENT, persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
 content TEXT NOT NULL, source_id TEXT REFERENCES sources(id) ON DELETE SET NULL,
 origin TEXT NOT NULL DEFAULT 'manual', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS memories_persona ON memories(persona_id, id);
CREATE TABLE IF NOT EXISTS profile_versions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
 profile TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS profiles_persona ON profile_versions(persona_id, id);
"""


class Store:
    """SQLite data directory, with a fresh connection per operation for worker safety."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / 'companion.sqlite3'
        with self.connection() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(str(self.db_path), timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _dicts(rows):
        return [dict(row) for row in rows]

    def create_persona(self, name, profile='', **kwargs):
        name = name.strip()
        if not name:
            raise ValueError('角色名称不能为空')
        persona_id = uuid.uuid4().hex
        with self.connection() as db:
            db.execute('INSERT INTO personas(id,name,profile) VALUES (?,?,?)', (persona_id, name, profile))
            db.execute('INSERT INTO profile_versions(persona_id,profile,reason) VALUES (?,?,?)', (persona_id, profile, '创建角色'))
        if kwargs:
            self.update_persona(persona_id, **kwargs)
        return self.get_persona(persona_id)

    def get_persona(self, persona_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM personas WHERE id=?', (persona_id,)).fetchone()
            return dict(row) if row else None

    def list_personas(self):
        with self.connection() as db:
            return self._dicts(db.execute('SELECT * FROM personas ORDER BY created_at,id'))

    def update_persona(self, persona_id, **changes):
        allowed = {'name', 'avatar', 'voice_mode', 'voice_name', 'voice_ref', 'voice_text', 'auto_voice'}
        if not changes or set(changes) - allowed:
            raise ValueError('无效的角色设置')
        if 'name' in changes and not str(changes['name']).strip():
            raise ValueError('角色名称不能为空')
        with self.connection() as db:
            result = db.execute('UPDATE personas SET ' + ','.join(k+'=?' for k in changes) + ' WHERE id=?', (*changes.values(), persona_id))
            if not result.rowcount:
                raise KeyError('角色不存在')
        return self.get_persona(persona_id)

    def delete_persona(self, persona_id, delete_files=True):
        with self.connection() as db:
            deleted = bool(db.execute('DELETE FROM personas WHERE id=?', (persona_id,)).rowcount)
        if deleted and delete_files:
            base = (self.data_dir / 'attachments').resolve()
            folder = base / persona_id
            if folder.is_symlink():
                folder.unlink()
            elif folder.exists():
                if folder.parent.resolve() != base:
                    raise ValueError('角色附件目录不安全')
                shutil.rmtree(folder)
        return deleted

    def save_profile(self, persona_id, profile, reason='手动编辑'):
        with self.connection() as db:
            if not db.execute('UPDATE personas SET profile=? WHERE id=?', (profile, persona_id)).rowcount:
                raise KeyError('角色不存在')
            db.execute('INSERT INTO profile_versions(persona_id,profile,reason) VALUES (?,?,?)', (persona_id, profile, reason))

    def list_profile_versions(self, persona_id):
        with self.connection() as db:
            return self._dicts(db.execute('SELECT * FROM profile_versions WHERE persona_id=? ORDER BY id DESC', (persona_id,)))

    def restore_profile(self, persona_id, version_id):
        with self.connection() as db:
            row = db.execute('SELECT profile FROM profile_versions WHERE id=? AND persona_id=?', (version_id, persona_id)).fetchone()
        if not row:
            raise KeyError('角色设定版本不存在')
        self.save_profile(persona_id, row['profile'], '恢复历史版本')
        return row['profile']

    def add_message(self, persona_id, role, content='', media_path='', media_type='', source_id=None, timestamp=None):
        if role not in ('user', 'assistant', 'system'):
            raise ValueError('无效的发言方')
        with self.connection() as db:
            if source_id and not db.execute('SELECT 1 FROM sources WHERE id=? AND persona_id=?', (source_id, persona_id)).fetchone():
                raise ValueError('资料来源不属于当前角色')
            cur = db.execute('INSERT INTO messages(persona_id,role,content,media_path,media_type,source_id,timestamp) VALUES (?,?,?,?,?,?,COALESCE(?,CURRENT_TIMESTAMP))', (persona_id, role, content, media_path, media_type, source_id, timestamp))
            return cur.lastrowid

    def add_messages(self, persona_id, messages, source_id=None):
        """Insert a batch of message dictionaries atomically."""
        with self.connection() as db:
            if source_id and not db.execute('SELECT 1 FROM sources WHERE id=? AND persona_id=?', (source_id, persona_id)).fetchone():
                raise ValueError('资料来源不属于当前角色')
            count = 0
            for m in messages:
                role = m['role']
                if role not in ('user', 'assistant', 'system'):
                    raise ValueError('无效的发言方')
                db.execute('INSERT INTO messages(persona_id,role,content,media_path,media_type,source_id,timestamp) VALUES (?,?,?,?,?,?,COALESCE(?,CURRENT_TIMESTAMP))', (persona_id, role, m.get('content',''), m.get('media_path',''), m.get('media_type',''), source_id, m.get('timestamp')))
                count += 1
            return count

    def list_messages(self, persona_id, limit=100, before_id=None, query=''):
        sql = 'SELECT * FROM messages WHERE persona_id=?'
        args = [persona_id]
        if before_id is not None:
            sql += ' AND id<?'
            args.append(before_id)
        if query:
            sql += " AND content LIKE ? ESCAPE '\\'"
            args.append('%' + self._escape_like(query) + '%')
        sql += ' ORDER BY id DESC LIMIT ?'
        args.append(max(1, min(int(limit), 1000)))
        with self.connection() as db:
            return list(reversed(self._dicts(db.execute(sql, args))))

    def delete_message(self, persona_id, message_id):
        with self.connection() as db:
            return bool(db.execute('DELETE FROM messages WHERE id=? AND persona_id=?', (message_id, persona_id)).rowcount)

    def add_source(self, persona_id, name, content_hash='', kind='', copied_path='', status='complete'):
        # Empty hashes are unique per import; real hashes provide character-scoped deduplication.
        source_id = uuid.uuid4().hex
        content_hash = content_hash or 'unhashed:' + source_id
        with self.connection() as db:
            db.execute('INSERT INTO sources(id,persona_id,name,content_hash,kind,copied_path,status) VALUES (?,?,?,?,?,?,?)', (source_id, persona_id, name, content_hash, kind, copied_path, status))
        return source_id

    def find_source(self, persona_id, content_hash):
        with self.connection() as db:
            row = db.execute('SELECT * FROM sources WHERE persona_id=? AND content_hash=?', (persona_id, content_hash)).fetchone()
            return dict(row) if row else None

    def list_sources(self, persona_id):
        with self.connection() as db:
            return self._dicts(db.execute('SELECT * FROM sources WHERE persona_id=? ORDER BY created_at DESC', (persona_id,)))

    def delete_source(self, persona_id, source_id, delete_messages=False, delete_memories=False, delete_copy=False):
        with self.connection() as db:
            source = db.execute('SELECT * FROM sources WHERE id=? AND persona_id=?', (source_id, persona_id)).fetchone()
            if not source:
                return False
            if delete_messages:
                db.execute('DELETE FROM messages WHERE persona_id=? AND source_id=?', (persona_id, source_id))
            if delete_memories:
                db.execute('DELETE FROM memories WHERE persona_id=? AND source_id=?', (persona_id, source_id))
            db.execute('DELETE FROM sources WHERE id=? AND persona_id=?', (source_id, persona_id))
        if delete_copy and source['copied_path']:
            # A shared, content-addressed file can be referenced by another source or message.
            with self.connection() as db:
                used = db.execute('SELECT 1 FROM sources WHERE copied_path=? UNION ALL SELECT 1 FROM messages WHERE media_path=? LIMIT 1', (source['copied_path'], source['copied_path'])).fetchone()
            if not used:
                path = self.resolve_attachment(source['copied_path'])
                if path.is_file():
                    path.unlink()
        return True

    def add_memory(self, persona_id, content, source_id=None, origin='manual'):
        if not content.strip():
            raise ValueError('记忆内容不能为空')
        with self.connection() as db:
            if source_id and not db.execute('SELECT 1 FROM sources WHERE id=? AND persona_id=?', (source_id, persona_id)).fetchone():
                raise ValueError('资料来源不属于当前角色')
            return db.execute('INSERT INTO memories(persona_id,content,source_id,origin) VALUES (?,?,?,?)', (persona_id, content.strip(), source_id, origin)).lastrowid

    def list_memories(self, persona_id, query=''):
        with self.connection() as db:
            if query:
                return self._dicts(db.execute("SELECT * FROM memories WHERE persona_id=? AND content LIKE ? ESCAPE '\\' ORDER BY id DESC", (persona_id, '%' + self._escape_like(query) + '%')))
            return self._dicts(db.execute('SELECT * FROM memories WHERE persona_id=? ORDER BY id DESC', (persona_id,)))

    @staticmethod
    def _escape_like(value):
        return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')

    def search_memories(self, persona_id, query, limit=8):
        # Chinese has no spaces; bigrams provide a useful deterministic local fallback.
        text = query.strip().lower()
        terms = set(re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]{2,}', text))
        for segment in re.findall(r'[\u4e00-\u9fff]+', text):
            terms.update(segment[i:i+2] for i in range(len(segment)-1))
            if len(segment) == 1:
                terms.add(segment)
        if not terms:
            return []
        memories = [m for m in self.list_memories(persona_id) if m['origin'] != 'machine-unconfirmed']
        ranked = [(sum(term in m['content'].lower() for term in terms), m) for m in memories]
        return [m for score, m in sorted(ranked, key=lambda x: (x[0], x[1]['id']), reverse=True) if score][:max(0, limit)]

    def search_imported_messages(self, persona_id, query, limit=6):
        """Return a small set of related historical utterances for this persona only."""
        segments = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]{3,}', query.lower())
        terms = []
        for segment in segments:
            candidates = [segment] if len(segment) <= 4 else [segment[:3], segment[-3:]]
            terms.extend(x for x in candidates if len(x) >= 2 and x not in terms)
        terms = terms[:4]
        if not terms: return []
        with self.connection() as db:
            predicate = ' OR '.join("content LIKE ? ESCAPE '\\'" for _ in terms)
            rows = db.execute(f'SELECT * FROM messages WHERE persona_id=? AND source_id IS NOT NULL AND ({predicate}) ORDER BY id DESC LIMIT ?',
                [persona_id, *['%' + self._escape_like(t) + '%' for t in terms], max(1, min(limit,20))])
            return self._dicts(rows)

    def sticker_candidates(self, persona_id, limit=12):
        with self.connection() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_recognition'").fetchone(): return []
            rows=db.execute("""SELECT r.media_path,m.content FROM media_recognition r
                JOIN memories m ON m.id=r.memory_id LEFT JOIN messages msg ON msg.id=r.message_id
                WHERE r.persona_id=? AND r.kind='image' AND r.status='confirmed'
                AND (msg.role='assistant' OR msg.id IS NULL) ORDER BY r.id DESC LIMIT ?""",(persona_id,limit))
            return self._dicts(rows)

    def update_memory(self, persona_id, memory_id, content):
        if not content.strip():
            raise ValueError('记忆内容不能为空')
        with self.connection() as db:
            return bool(db.execute('UPDATE memories SET content=? WHERE id=? AND persona_id=?', (content.strip(), memory_id, persona_id)).rowcount)

    def delete_memory(self, persona_id, memory_id):
        with self.connection() as db:
            return bool(db.execute('DELETE FROM memories WHERE id=? AND persona_id=?', (memory_id, persona_id)).rowcount)

    def copy_attachment(self, persona_id, source_path):
        """Copy and deduplicate an attachment within a role. Returns relative path."""
        if not self.get_persona(persona_id):
            raise KeyError('角色不存在')
        source = Path(source_path)
        if not source.is_file():
            raise FileNotFoundError(str(source))
        digest = hashlib.sha256()
        with source.open('rb') as f:
            for chunk in iter(lambda: f.read(1024*1024), b''):
                digest.update(chunk)
        suffix = re.sub(r'[^.a-zA-Z0-9]', '', source.suffix)[:12].lower()
        folder = self.data_dir / 'attachments' / persona_id
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / (digest.hexdigest() + suffix)
        if not destination.exists():
            tmp = folder / (uuid.uuid4().hex + '.tmp')
            try:
                shutil.copyfile(source, tmp)
                os.replace(tmp, destination)
            finally:
                tmp.unlink(missing_ok=True)
        return destination.relative_to(self.data_dir).as_posix()

    def resolve_attachment(self, relative_path):
        path = (self.data_dir / relative_path).resolve()
        if not path.is_relative_to((self.data_dir / 'attachments').resolve()):
            raise ValueError('附件路径不安全')
        return path

    def backup(self, destination):
        """Create a portable ZIP using a consistent SQLite snapshot."""
        destination = Path(destination).expanduser().resolve()
        if destination.is_relative_to(self.data_dir):
            raise ValueError('备份文件请放在数据目录之外')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp) / 'companion.sqlite3'
            with self.connection() as db:
                with closing(sqlite3.connect(str(snapshot))) as output:
                    db.backup(output)
            with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
                z.write(snapshot, 'companion.sqlite3')
                folder = self.data_dir / 'attachments'
                if folder.exists():
                    for path in folder.rglob('*'):
                        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(folder.resolve()):
                            z.write(path, path.relative_to(self.data_dir).as_posix())
        return str(destination)

    @staticmethod
    def restore_backup(archive, destination):
        """Restore only into an empty directory and reject path traversal."""
        destination = Path(destination).expanduser().resolve()
        if destination.exists() and any(destination.iterdir()):
            raise ValueError('恢复目标目录必须为空，避免覆盖已有数据')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            if 'companion.sqlite3' not in names:
                raise ValueError('备份不包含数据库')
            for item in z.infolist():
                path = (destination / item.filename).resolve()
                mode = item.external_attr >> 16
                if not path.is_relative_to(destination) or (mode & 0o170000) == 0o120000:
                    raise ValueError('备份包含不安全的路径')
                if item.filename != 'companion.sqlite3' and not item.filename.startswith('attachments/'):
                    raise ValueError('备份包含未知文件')
            with tempfile.TemporaryDirectory(dir=str(destination.parent), prefix='.companion-restore-') as temp:
                staging = Path(temp) / 'data'
                staging.mkdir()
                z.extractall(staging)
                try:
                    with closing(sqlite3.connect(str(staging / 'companion.sqlite3'))) as db:
                        check = db.execute('PRAGMA integrity_check').fetchone()[0]
                        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                        if check != 'ok' or not {'personas', 'messages', 'memories', 'sources', 'profile_versions'} <= tables:
                            raise ValueError('备份数据库校验失败')
                except sqlite3.DatabaseError as exc:
                    raise ValueError('备份数据库校验失败') from exc
                if destination.exists():
                    destination.rmdir()  # Fails safely if another process added a file.
                staging.rename(destination)
        return Store(destination)
