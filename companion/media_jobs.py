"""Reviewable, local-only recognition of imported image/audio attachments.

The caller runs ``run_media_jobs`` in a background thread. Model output is
stored as unconfirmed memory and must be excluded from chat retrieval until a
person explicitly accepts it. This module never communicates over a network.
"""
from __future__ import annotations

from pathlib import Path

from .importer import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS


_SCHEMA = """
CREATE TABLE IF NOT EXISTS media_recognition (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
 source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
 message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
 media_path TEXT NOT NULL,
 kind TEXT NOT NULL,
 memory_id INTEGER REFERENCES memories(id) ON DELETE SET NULL,
 status TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(persona_id, media_path)
);
"""


def _ensure_schema(store):
    with store.connection() as db:
        db.execute(_SCHEMA)


def _kind(path):
    ext = Path(path).suffix.lower()
    return 'image' if ext in IMAGE_EXTENSIONS else 'audio' if ext in AUDIO_EXTENSIONS else ''


def _list_candidates(store, persona_id, source_id=None):
    with store.connection() as db:
        args = [persona_id]
        condition = ''
        if source_id:
            condition = ' AND s.id=?'
            args.append(source_id)
        sources = db.execute('SELECT s.* FROM sources s WHERE s.persona_id=?' + condition, args).fetchall()
        result = {}
        for source in sources:
            rows = db.execute('SELECT id, media_path FROM messages WHERE persona_id=? AND source_id=? AND media_path<>\'\'',
                              (persona_id, source['id'])).fetchall()
            for row in rows:
                kind = _kind(row['media_path'])
                if kind:
                    result.setdefault(row['media_path'], {'source_id': source['id'], 'message_id': row['id'],
                                                          'path': row['media_path'], 'kind': kind})
            # Standalone media is kept as a source rather than invented chat.
            copied = source['copied_path']
            kind = _kind(copied)
            if copied and kind:
                result.setdefault(copied, {'source_id': source['id'], 'message_id': None,
                                           'path': copied, 'kind': kind})
        return list(result.values())


def run_media_jobs(store, models, persona_id, source_id=None, progress=None, cancel_event=None):
    """Recognize imported media serially. Run in a worker thread.

    ``progress(done, total, path)`` is optional. ``cancel_event`` is any object
    with an ``is_set()`` method; cancellation is checked between images and is
    also forwarded to audio recognition. Prior successful work remains saved.
    Returns counts, error messages and cancellation status; existing pending,
    accepted or rejected results are not recognized a second time.
    """
    _ensure_schema(store)
    if not store.get_persona(persona_id):
        raise KeyError('角色不存在')
    if source_id and not any(x['id'] == source_id for x in store.list_sources(persona_id)):
        raise ValueError('资料来源不属于当前角色')
    candidates = _list_candidates(store, persona_id, source_id)
    summary = {'total': len(candidates), 'processed': 0, 'skipped': 0,
               'failed': 0, 'errors': [], 'cancelled': False}
    for index, item in enumerate(candidates):
        if cancel_event and cancel_event.is_set():
            summary['cancelled'] = True
            break
        if progress:
            progress(index, len(candidates), item['path'])
        with store.connection() as db:
            existing = db.execute('SELECT 1 FROM media_recognition WHERE persona_id=? AND media_path=?',
                                  (persona_id, item['path'])).fetchone()
        if existing:
            summary['skipped'] += 1
            continue
        try:
            path = store.resolve_attachment(item['path'])
            if not path.is_file():
                raise FileNotFoundError('附件不存在')
            if item['kind'] == 'image':
                result = models.describe_image(str(path))
            else:
                result = models.transcribe(str(path), cancel_event=cancel_event)
            if cancel_event and cancel_event.is_set():
                summary['cancelled'] = True
                break
            result = str(result or '').strip()
            if not result:
                raise ValueError('未识别出内容')
            label = '图片描述' if item['kind'] == 'image' else '语音转写'
            with store.connection() as db:
                # A competing worker may have finished this attachment meanwhile.
                if db.execute('SELECT 1 FROM media_recognition WHERE persona_id=? AND media_path=?',
                              (persona_id, item['path'])).fetchone():
                    summary['skipped'] += 1
                    continue
                memory = db.execute('INSERT INTO memories(persona_id,content,source_id,origin) VALUES (?,?,?,?)',
                                    (persona_id, f'【机器识别，尚未确认 · {label}】{result}',
                                     item['source_id'], 'machine-unconfirmed')).lastrowid
                db.execute('INSERT INTO media_recognition(persona_id,source_id,message_id,media_path,kind,memory_id,status) VALUES (?,?,?,?,?,?,?)',
                           (persona_id, item['source_id'], item['message_id'], item['path'], item['kind'], memory, 'pending'))
            summary['processed'] += 1
        except Exception as exc:
            if cancel_event and cancel_event.is_set():
                summary['cancelled'] = True
                break
            summary['failed'] += 1
            summary['errors'].append(f'{item["path"]}: {exc}')
            # Stop on error, so missing model files do not cause thousands of
            # repeated expensive attempts. The source remains retryable.
            break
    if progress:
        progress(summary['processed'] + summary['skipped'], len(candidates), '')
    return summary


def list_pending(store, persona_id):
    """Return unconfirmed results and their originating source and attachment."""
    _ensure_schema(store)
    with store.connection() as db:
        rows = db.execute("""SELECT r.id,r.source_id,r.message_id,r.media_path,r.kind,r.memory_id,
                          m.content,s.name AS source_name FROM media_recognition r
                          JOIN memories m ON m.id=r.memory_id
                          JOIN sources s ON s.id=r.source_id
                          WHERE r.persona_id=? AND r.status='pending' ORDER BY r.id""", (persona_id,))
        return [dict(row) for row in rows]


def confirm_recognition(store, persona_id, memory_id, corrected_text):
    """Accept an edited result, preserving the media/source link and label."""
    _ensure_schema(store)
    text = str(corrected_text or '').strip()
    if not text:
        raise ValueError('确认内容不能为空。')
    # Strip the standard pending marker if the UI starts from memory.content.
    if text.startswith('【机器识别，尚未确认 · ') and '】' in text:
        text = text.split('】', 1)[1].strip()
    if not text:
        raise ValueError('确认内容不能为空。')
    with store.connection() as db:
        row = db.execute("SELECT * FROM media_recognition WHERE persona_id=? AND memory_id=? AND status='pending'",
                         (persona_id, memory_id)).fetchone()
        if not row:
            raise KeyError('待确认的识别结果不存在。')
        label = '图片描述' if row['kind'] == 'image' else '语音转写'
        db.execute("UPDATE memories SET content=?,origin='machine-confirmed' WHERE id=? AND persona_id=?",
                   (f'【机器识别，经用户确认 · {label}】{text}', memory_id, persona_id))
        db.execute("UPDATE media_recognition SET status='confirmed' WHERE id=?", (row['id'],))


def reject_recognition(store, persona_id, memory_id):
    """Discard an unconfirmed result without deleting the original attachment."""
    _ensure_schema(store)
    with store.connection() as db:
        row = db.execute("SELECT * FROM media_recognition WHERE persona_id=? AND memory_id=? AND status='pending'",
                         (persona_id, memory_id)).fetchone()
        if not row:
            raise KeyError('待确认的识别结果不存在。')
        db.execute("UPDATE media_recognition SET status='rejected',memory_id=NULL WHERE id=?", (row['id'],))
        db.execute('DELETE FROM memories WHERE id=? AND persona_id=?', (memory_id, persona_id))
