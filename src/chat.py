"""
Chat window — calls Anthropic /v1/messages directly so Chinese IME works.
Supports streaming, multi-turn history, markdown-style code rendering.
"""
import json
import urllib.request
import urllib.error
import threading
import re

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTextEdit, QPlainTextEdit, QLabel, QScrollArea,
    QFrame, QSizePolicy, QApplication, QComboBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject, QTimer, QSize
from PyQt6.QtGui import QFont, QColor, QTextCursor, QKeyEvent


# ── model list ────────────────────────────────────────────────────────────────

MODELS = [
    ("claude-opus-4-5",          "Opus 4.5"),
    ("claude-sonnet-4-5",        "Sonnet 4.5"),
    ("claude-haiku-4-5-20251001","Haiku 4.5"),
    ("claude-opus-4-7",          "Opus 4.7"),
    ("claude-sonnet-4-6",        "Sonnet 4.6"),
]


# ── streaming worker ──────────────────────────────────────────────────────────

class StreamWorker(QObject):
    token  = pyqtSignal(str)       # incremental text
    done   = pyqtSignal()
    error  = pyqtSignal(str)

    def __init__(self, api_key: str, base_url: str, model: str, messages: list):
        super().__init__()
        self._api_key  = api_key
        self._base_url = base_url.rstrip("/")
        self._model    = model
        self._messages = messages

    def run(self):
        url  = f"{self._base_url}/v1/messages"
        body = json.dumps({
            "model":      self._model,
            "max_tokens": 8096,
            "stream":     True,
            "messages":   self._messages,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={
                "x-api-key":    self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                for raw in resp:
                    line = raw.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") == "content_block_delta":
                        delta = obj.get("delta", {})
                        if delta.get("type") == "text_delta":
                            self.token.emit(delta.get("text", ""))
        except urllib.error.HTTPError as e:
            body_bytes = e.read()
            try:
                msg = json.loads(body_bytes).get("error", {}).get("message", body_bytes.decode())
            except Exception:
                msg = body_bytes.decode(errors="replace")
            self.error.emit(f"HTTP {e.code}: {msg}")
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.done.emit()


# ── message bubble ────────────────────────────────────────────────────────────

class MessageBubble(QFrame):
    """Single chat message rendered with basic markdown (code blocks, bold)."""

    _DARK_USER      = "#3d4270"
    _DARK_ASSISTANT = "#2a2a3e"
    _LIGHT_USER     = "#dce4ff"
    _LIGHT_ASSISTANT= "#f4f4f8"

    def __init__(self, role: str, dark: bool, parent=None):
        super().__init__(parent)
        self._role = role
        self._dark = dark
        self._full_text = ""

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setLineWidth(0)
        self._apply_bg()

        vlay = QVBoxLayout(self)
        vlay.setContentsMargins(12, 8, 12, 8)
        vlay.setSpacing(4)

        # role label
        role_lbl = QLabel("You" if role == "user" else "Claude")
        role_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        role_lbl.setStyleSheet(
            f"color: {'#8899ff' if dark else '#4455cc'};"
        )
        vlay.addWidget(role_lbl)

        self._text_widget = QTextEdit()
        self._text_widget.setReadOnly(True)
        self._text_widget.setFrameShape(QFrame.Shape.NoFrame)
        self._text_widget.setFont(QFont("Segoe UI", 11))
        self._text_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._text_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._text_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._text_widget.setStyleSheet("background: transparent; border: none;")
        self._text_widget.document().contentsChanged.connect(self._fit_height)
        vlay.addWidget(self._text_widget)

        # copy button row
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setFixedSize(52, 22)
        self._copy_btn.setStyleSheet(
            "QPushButton{background:transparent;border:1px solid #666;"
            "border-radius:4px;font-size:10px;color:#888;}"
            "QPushButton:hover{color:#aaa;border-color:#aaa;}"
        )
        self._copy_btn.clicked.connect(self._copy_text)
        btn_row.addWidget(self._copy_btn)
        vlay.addLayout(btn_row)

    def _apply_bg(self):
        if self._dark:
            bg = self._DARK_USER if self._role == "user" else self._DARK_ASSISTANT
            fg = "#cdd6f4"
        else:
            bg = self._LIGHT_USER if self._role == "user" else self._LIGHT_ASSISTANT
            fg = "#1a1a2e"
        self.setStyleSheet(
            f"MessageBubble{{background:{bg};border-radius:10px;}}"
            f"QTextEdit{{color:{fg};}}"
        )

    def _fit_height(self):
        doc_h = int(self._text_widget.document().size().height()) + 4
        self._text_widget.setFixedHeight(max(doc_h, 24))

    def _copy_text(self):
        QApplication.clipboard().setText(self._full_text)
        self._copy_btn.setText("Copied!")
        QTimer.singleShot(1500, lambda: self._copy_btn.setText("Copy"))

    # public ──────────────────────────────────────────────────────────────────

    def set_text(self, text: str):
        self._full_text = text
        self._text_widget.setHtml(self._to_html(text))

    def append_text(self, chunk: str):
        self._full_text += chunk
        self._text_widget.setHtml(self._to_html(self._full_text))
        # keep cursor at end
        cur = self._text_widget.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        self._text_widget.setTextCursor(cur)

    # ── minimal markdown → html ───────────────────────────────────────────────

    @staticmethod
    def _to_html(text: str) -> str:
        # split on fenced code blocks
        parts = re.split(r"(```[\s\S]*?```)", text)
        html_parts = []
        for part in parts:
            if part.startswith("```"):
                inner = re.sub(r"^```[^\n]*\n?", "", part)
                inner = re.sub(r"```$", "", inner)
                inner = inner.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                html_parts.append(
                    f'<pre style="background:#1a1a2e;color:#a9dc76;'
                    f'padding:8px;border-radius:6px;'
                    f'font-family:Consolas,monospace;font-size:10pt;'
                    f'white-space:pre-wrap;word-wrap:break-word;">{inner}</pre>'
                )
            else:
                # inline code
                part = re.sub(
                    r"`([^`]+)`",
                    r'<code style="background:#1a1a2e;color:#a9dc76;'
                    r'padding:1px 4px;border-radius:3px;font-family:Consolas,monospace;">\1</code>',
                    part,
                )
                # bold
                part = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", part)
                # italic
                part = re.sub(r"\*(.+?)\*", r"<i>\1</i>", part)
                # headings
                part = re.sub(r"^### (.+)$", r"<h3>\1</h3>", part, flags=re.MULTILINE)
                part = re.sub(r"^## (.+)$",  r"<h2>\1</h2>", part, flags=re.MULTILINE)
                part = re.sub(r"^# (.+)$",   r"<h1>\1</h1>", part, flags=re.MULTILINE)
                # newlines
                part = part.replace("\n", "<br>")
                html_parts.append(f'<span style="line-height:1.6;">{part}</span>')
        return "".join(html_parts)


# ── send-box (Shift+Enter = newline, Enter = send) ────────────────────────────

class InputBox(QPlainTextEdit):
    submit = pyqtSignal()

    def keyPressEvent(self, e: QKeyEvent):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(e)
            else:
                self.submit.emit()
        else:
            super().keyPressEvent(e)


# ── chat window ───────────────────────────────────────────────────────────────

class ChatWindow(QWidget):
    def __init__(self, api_key: str, base_url: str, dark: bool = True, parent=None):
        super().__init__(parent)
        self._api_key  = api_key
        self._base_url = base_url
        self._dark     = dark
        self._history: list[dict] = []   # {"role": ..., "content": ...}
        self._busy     = False
        self._current_bubble: MessageBubble | None = None
        self._thread: QThread | None = None

        self.setWindowTitle("Claude Chat")
        self.resize(780, 680)
        self._build_ui()
        self._apply_theme()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        vlay = QVBoxLayout(self)
        vlay.setContentsMargins(0, 0, 0, 0)
        vlay.setSpacing(0)

        # toolbar
        toolbar = QFrame()
        toolbar.setFixedHeight(44)
        toolbar.setObjectName("toolbar")
        tbar = QHBoxLayout(toolbar)
        tbar.setContentsMargins(12, 0, 12, 0)

        title = QLabel("Claude Chat")
        title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        title.setStyleSheet("color: white;")
        tbar.addWidget(title)
        tbar.addStretch()

        tbar.addWidget(QLabel("Model:"))
        self._model_combo = QComboBox()
        self._model_combo.setFixedWidth(160)
        for mid, label in MODELS:
            self._model_combo.addItem(label, mid)
        self._model_combo.setCurrentIndex(1)   # default Sonnet 4.5
        tbar.addWidget(self._model_combo)

        clear_btn = QPushButton("New Chat")
        clear_btn.setFixedHeight(28)
        clear_btn.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.15);color:white;"
            "border:none;border-radius:5px;padding:0 10px;}"
            "QPushButton:hover{background:rgba(255,255,255,0.25);}"
        )
        clear_btn.clicked.connect(self._clear_chat)
        tbar.addWidget(clear_btn)
        vlay.addWidget(toolbar)

        # scroll area for messages
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._msg_container = QWidget()
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(12, 12, 12, 12)
        self._msg_layout.setSpacing(10)
        self._msg_layout.addStretch()
        self._scroll.setWidget(self._msg_container)
        vlay.addWidget(self._scroll, 1)

        # status bar
        self._status = QLabel("")
        self._status.setFixedHeight(18)
        self._status.setStyleSheet("color: #888; font-size: 10px; padding: 0 14px;")
        vlay.addWidget(self._status)

        # input area
        input_frame = QFrame()
        input_frame.setObjectName("inputFrame")
        ilay = QVBoxLayout(input_frame)
        ilay.setContentsMargins(12, 8, 12, 10)
        ilay.setSpacing(6)

        hint = QLabel("Enter 发送 · Shift+Enter 换行")
        hint.setStyleSheet("color: #666; font-size: 10px;")
        ilay.addWidget(hint)

        row = QHBoxLayout()
        self._input = InputBox()
        self._input.setPlaceholderText("输入消息，支持中文...")
        self._input.setFont(QFont("Segoe UI", 11))
        self._input.setFixedHeight(80)
        self._input.submit.connect(self._send)
        row.addWidget(self._input)

        self._send_btn = QPushButton("发送")
        self._send_btn.setFixedSize(64, 80)
        self._send_btn.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._send_btn.clicked.connect(self._send)
        row.addWidget(self._send_btn)
        ilay.addLayout(row)
        vlay.addWidget(input_frame)

    def _apply_theme(self):
        if self._dark:
            bg, card, fg = "#1e1e2e", "#2a2a3e", "#cdd6f4"
            accent = "#5b6af0"
            input_bg = "#2a2a3e"
        else:
            bg, card, fg = "#f0f0f5", "#ffffff", "#1a1a2e"
            accent = "#5b6af0"
            input_bg = "#ffffff"

        self.setStyleSheet(f"""
            QWidget {{ background: {bg}; color: {fg}; }}
            QFrame#toolbar {{ background: {accent}; }}
            QFrame#inputFrame {{ background: {card}; border-top: 1px solid #333; }}
            QScrollArea {{ background: {bg}; }}
            QPlainTextEdit {{
                background: {input_bg}; color: {fg};
                border: 1.5px solid #444; border-radius: 6px;
                padding: 6px;
            }}
            QPlainTextEdit:focus {{ border-color: {accent}; }}
            QPushButton#sendBtn {{
                background: {accent}; color: white;
                border: none; border-radius: 6px;
            }}
            QPushButton#sendBtn:hover {{ background: #404bc8; }}
            QPushButton#sendBtn:disabled {{ background: #444; color: #777; }}
            QComboBox {{
                background: rgba(255,255,255,0.15); color: white;
                border: none; border-radius: 4px; padding: 2px 6px;
            }}
        """)
        self._send_btn.setObjectName("sendBtn")
        self._send_btn.setStyleSheet(
            f"QPushButton{{background:{accent};color:white;border:none;border-radius:6px;}}"
            f"QPushButton:hover{{background:#404bc8;}}"
            f"QPushButton:disabled{{background:#444;color:#777;}}"
        )

    # ── chat logic ────────────────────────────────────────────────────────────

    def _clear_chat(self):
        self._history.clear()
        # remove all bubbles (keep the stretch at index 0)
        while self._msg_layout.count() > 1:
            item = self._msg_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        self._status.setText("")

    def _add_bubble(self, role: str) -> MessageBubble:
        bubble = MessageBubble(role, self._dark)
        # insert before the trailing stretch
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, bubble)
        QTimer.singleShot(50, self._scroll_to_bottom)
        return bubble

    def _scroll_to_bottom(self):
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _send(self):
        if self._busy:
            return
        text = self._input.toPlainText().strip()
        if not text:
            return
        self._input.clear()

        # user bubble
        user_bubble = self._add_bubble("user")
        user_bubble.set_text(text)
        self._history.append({"role": "user", "content": text})

        # assistant bubble (streaming)
        self._current_bubble = self._add_bubble("assistant")
        self._current_bubble.set_text("▌")
        self._set_busy(True)
        self._status.setText("Claude 正在思考...")

        model = self._model_combo.currentData()
        worker = StreamWorker(
            self._api_key, self._base_url, model, list(self._history)
        )
        thread = QThread()
        worker.moveToThread(thread)
        worker.token.connect(self._on_token)
        worker.done.connect(self._on_done)
        worker.error.connect(self._on_error)
        thread.started.connect(worker.run)
        thread.start()
        self._thread = thread
        self._worker = worker
        self._accumulated = ""

    def _on_token(self, chunk: str):
        self._accumulated += chunk
        if self._current_bubble:
            self._current_bubble.set_text(self._accumulated + "▌")
        QTimer.singleShot(20, self._scroll_to_bottom)

    def _on_done(self):
        if self._current_bubble:
            self._current_bubble.set_text(self._accumulated)
        self._history.append({"role": "assistant", "content": self._accumulated})
        self._accumulated = ""
        self._current_bubble = None
        self._set_busy(False)
        self._status.setText("")
        if self._thread:
            self._thread.quit()

    def _on_error(self, msg: str):
        if self._current_bubble:
            self._current_bubble.set_text(f"❌ 错误：{msg}")
        self._set_busy(False)
        self._status.setText("")
        if self._thread:
            self._thread.quit()

    def _set_busy(self, busy: bool):
        self._busy = busy
        self._send_btn.setEnabled(not busy)
        self._input.setEnabled(not busy)
