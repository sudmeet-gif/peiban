"""Import dialog: explicit sender mapping and cancellable background work."""
from __future__ import annotations

import uuid
from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QProgressBar, QTextEdit, QVBoxLayout,
)

from .importer import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, copy_import, generate_style_profile, inspect_file, iter_records


class ImportCancelled(Exception):
    pass


class ImportWorker(QThread):
    progress = Signal(int, str)
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, store, persona_id, path, preview, character, user, parent=None, generate_profile=True):
        super().__init__(parent)
        self.store, self.persona_id = store, persona_id
        self.path, self.preview = Path(path), preview
        self.character, self.user = character, user
        self.generate_profile = generate_profile
        self.cancel_event = Event()
        self.pause_event = Event()

    def cancel(self):
        self.cancel_event.set()

    def checkpoint(self):
        while self.pause_event.is_set() and not self.cancel_event.wait(0.1):
            pass
        return self.cancel_event.is_set()

    def run(self):
        try:
            existing = self.store.find_source(self.persona_id, self.preview['sha256'])
            if existing:
                raise ValueError('此角色已经导入过这份资料。')
            destination = self.store.data_dir / 'attachments' / self.persona_id / 'imports'
            copied = copy_import(self.path, destination,
                progress=lambda done, total, name: self.progress.emit(int(60 * done / max(total, 1)), f'正在复制附件 {done}/{total}'),
                cancelled=self.checkpoint)
            if copied['cancelled'] or self.checkpoint():
                raise ImportCancelled()
            source_path = Path(copied['source'])
            draft = ''
            if self.generate_profile and self.character:
                self.progress.emit(60, '正在从角色发言中整理语气草稿…')
                draft = generate_style_profile(iter_records(source_path), self.character)
                if self.checkpoint():
                    raise ImportCancelled()
            relative_source = source_path.relative_to(self.store.data_dir).as_posix()
            source_id = uuid.uuid4().hex
            count = 0
            skipped = 0
            # One transaction ensures that cancellation or errors do not leave
            # a source marked complete with only part of its messages saved.
            with self.store.connection() as db:
                db.execute('INSERT INTO sources(id,persona_id,name,content_hash,kind,copied_path,status) VALUES (?,?,?,?,?,?,?)',
                           (source_id, self.persona_id, self.path.name, copied['sha256'], self.path.suffix.lower(), relative_source, 'complete'))
                for i, record in enumerate(iter_records(source_path)):
                    if self.checkpoint():
                        raise ImportCancelled()
                    sender = record['sender']
                    role = 'assistant' if sender and sender == self.character else 'user' if sender and sender == self.user else None
                    if role:
                        valid = [x for x in record['attachments'] if x['exists']]
                        attachment_paths = []
                        for item in valid:
                            absolute = (source_path.parent / item['path']).resolve()
                            if absolute.is_relative_to(source_path.parent.resolve()):
                                attachment_paths.append(absolute.relative_to(self.store.data_dir).as_posix())
                        attachments = attachment_paths or ['']
                        for j, media in enumerate(attachments):
                            suffix = Path(media).suffix.lower()
                            kind = 'image' if suffix in IMAGE_EXTENSIONS else 'audio' if suffix in AUDIO_EXTENSIONS else ''
                            db.execute('INSERT INTO messages(persona_id,role,content,media_path,media_type,source_id,timestamp) VALUES (?,?,?,?,?,?,COALESCE(?,CURRENT_TIMESTAMP))',
                                       (self.persona_id, role, record['text'] if j == 0 else '', media, kind, source_id, record['timestamp'] or None))
                        count += 1
                    elif not sender and record['text'].strip():
                        # An unstructured document is source material, not a
                        # fabricated past conversation with the user.
                        text = record['text'].strip()
                        for offset in range(0, len(text), 2000):
                            db.execute('INSERT INTO memories(persona_id,content,source_id,origin) VALUES (?,?,?,?)',
                                       (self.persona_id, text[offset:offset + 2000], source_id, 'imported'))
                        count += 1
                    elif sender:
                        skipped += 1
                    if i % 200 == 0:
                        self.progress.emit(60 + int(40 * (i + 1) / max(1, self.preview['message_count'])), f'正在保存资料 {i + 1}/{self.preview["message_count"]}')
            self.completed.emit({'count': count, 'skipped': skipped, 'missing': copied['missing'], 'source_id': source_id, 'profile_draft': draft})
        except ImportCancelled:
            self.failed.emit('导入已取消。已复制的临时资料可在下次导入时继续复用，数据库没有保存不完整记录。')
        except Exception as exc:
            self.failed.emit(str(exc))


class ImportDialog(QDialog):
    imported = Signal()

    def __init__(self, store, persona_id, parent=None):
        super().__init__(parent)
        self.store, self.persona_id = store, persona_id
        self.path = None
        self.preview = None
        self.worker = None
        self.setWindowTitle('导入角色资料')
        self.resize(600, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('选择资料并确认聊天记录中双方的发言人。原文件不会被修改。'))
        choose = QPushButton('选择文件')
        choose.clicked.connect(self.choose_file)
        layout.addWidget(choose)
        self.file_label = QLabel('尚未选择文件')
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        row = QHBoxLayout()
        row.addWidget(QLabel('角色的发言人'))
        self.character = QComboBox()
        row.addWidget(self.character)
        row.addWidget(QLabel('我的发言人'))
        self.user = QComboBox()
        row.addWidget(self.user)
        layout.addLayout(row)
        self.status = QLabel('无发言人标记的文档会作为资料记忆保存，图片和音频会作为资料保存。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.make_profile = QCheckBox('从角色发言生成语气草稿，追加到现有设定（可在角色设置中修改）')
        self.make_profile.setChecked(True)
        layout.addWidget(self.make_profile)
        self.bar = QProgressBar()
        self.bar.setValue(0)
        layout.addWidget(self.bar)
        buttons = QHBoxLayout()
        self.start_button = QPushButton('开始导入')
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_import)
        self.pause_button = QPushButton('暂停')
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self.toggle_pause)
        self.cancel_button = QPushButton('关闭')
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.pause_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

    def choose_file(self):
        if self.worker and self.worker.isRunning():
            return
        default = Path.home() / 'Desktop' / 'MARKDOWN'
        name, _ = QFileDialog.getOpenFileName(self, '选择聊天记录或素材', str(default if default.exists() else Path.home()),
            '支持的文件 (*.md *.markdown *.txt *.docx *.doc *.jpg *.jpeg *.png *.gif *.webp *.bmp *.wav *.mp3 *.m4a *.aac *.ogg *.flac *.opus);;所有文件 (*)')
        if not name:
            return
        try:
            preview = inspect_file(name)
        except Exception as exc:
            QMessageBox.warning(self, '无法读取资料', str(exc))
            return
        self.path, self.preview = Path(name), preview
        self.file_label.setText(str(self.path))
        summary = [f'识别到 {preview["message_count"]} 条记录，{preview["attachment_count"]} 个附件。']
        summary.extend(f'{sender}: {count} 条' for sender, count in preview['senders'].items())
        if preview['missing']:
            summary.append(f'缺失附件 {len(preview["missing"])} 个：')
            summary.extend(preview['missing'][:50])
        self.details.setPlainText('\n'.join(summary))
        for combo in (self.character, self.user):
            combo.clear()
            combo.addItem('请选择', None)
            for sender in preview['senders']:
                combo.addItem(sender, sender)
            combo.setEnabled(bool(preview['senders']))
        self.start_button.setEnabled(True)

    def start_import(self):
        if not self.preview:
            return
        character, user = self.character.currentData(), self.user.currentData()
        if self.preview['senders'] and (not character or not user or character == user):
            QMessageBox.warning(self, '请确认发言人', '请选择不同的角色发言人和自己的发言人；其他发言人将被跳过。')
            return
        self.start_button.setEnabled(False)
        self.cancel_button.setText('取消导入')
        self.pause_button.setText('暂停')
        self.pause_button.setEnabled(True)
        self.worker = ImportWorker(self.store, self.persona_id, self.path, self.preview, character, user, self,
                                   generate_profile=self.make_profile.isChecked())
        self.worker.progress.connect(self.on_progress)
        self.worker.completed.connect(self.on_completed)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def on_progress(self, value, message):
        self.bar.setValue(value)
        self.status.setText(message)

    def toggle_pause(self):
        if not self.worker or not self.worker.isRunning():
            return
        if self.worker.pause_event.is_set():
            self.worker.pause_event.clear()
            self.pause_button.setText('暂停')
            self.status.setText('继续导入……')
        else:
            self.worker.pause_event.set()
            self.pause_button.setText('继续')
            self.status.setText('将在当前文件处理完成后暂停。')

    def on_completed(self, result):
        self.bar.setValue(100)
        self.cancel_button.setText('关闭')
        self.pause_button.setEnabled(False)
        message = f'导入完成：{result["count"]} 条，跳过 {result["skipped"]} 条，缺失附件 {len(result["missing"])} 个。'
        draft = result.get('profile_draft', '')
        if draft:
            try:
                persona = self.store.get_persona(self.persona_id)
                existing = persona['profile'].strip()
                self.store.save_profile(self.persona_id, (existing + '\n\n' if existing else '') + draft, '根据导入资料生成语气草稿')
                message += ' 已追加语气草稿，可在角色设置中修改或恢复。'
            except Exception as exc:
                message += f' 语气草稿保存失败：{exc}'
        self.status.setText(message)
        self.imported.emit()

    def on_failed(self, message):
        self.start_button.setEnabled(True)
        self.cancel_button.setText('关闭')
        self.pause_button.setEnabled(False)
        self.status.setText(message)

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.status.setText('正在取消，请稍候……')
            return
        super().reject()
