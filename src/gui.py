"""
PyQt6-based GUI for Claude Code Launcher.
"""
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QProgressBar,
    QPlainTextEdit, QGroupBox, QFileDialog, QFrame,
    QApplication, QDialog, QCheckBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QPalette, QColor

import src.config as config
import src.installer as installer
from src.chat import ChatWindow


# ── worker thread ─────────────────────────────────────────────────────────────

class Worker(QObject):
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, task):
        super().__init__()
        self._task = task   # callable(progress_cb) -> (bool, str)

    def run(self):
        ok, msg = self._task(self.progress.emit)
        self.finished.emit(ok, msg)


# ── colour helpers ────────────────────────────────────────────────────────────

_DARK_BG   = "#1e1e2e"
_DARK_CARD = "#2a2a3e"
_DARK_TEXT = "#cdd6f4"
_ACCENT    = "#5b6af0"
_GREEN     = "#2ecc71"
_RED       = "#e74c3c"
_YELLOW    = "#f39c12"

_LIGHT_BG   = "#f0f0f5"
_LIGHT_CARD = "#ffffff"
_LIGHT_TEXT = "#1a1a2e"


def _dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor(_DARK_BG))
    p.setColor(QPalette.ColorRole.WindowText,      QColor(_DARK_TEXT))
    p.setColor(QPalette.ColorRole.Base,            QColor(_DARK_CARD))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor(_DARK_BG))
    p.setColor(QPalette.ColorRole.Text,            QColor(_DARK_TEXT))
    p.setColor(QPalette.ColorRole.Button,          QColor(_DARK_CARD))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor(_DARK_TEXT))
    p.setColor(QPalette.ColorRole.Highlight,       QColor(_ACCENT))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor("#666688"))
    return p


def _light_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor(_LIGHT_BG))
    p.setColor(QPalette.ColorRole.WindowText,      QColor(_LIGHT_TEXT))
    p.setColor(QPalette.ColorRole.Base,            QColor(_LIGHT_CARD))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor(_LIGHT_BG))
    p.setColor(QPalette.ColorRole.Text,            QColor(_LIGHT_TEXT))
    p.setColor(QPalette.ColorRole.Button,          QColor(_LIGHT_CARD))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor(_LIGHT_TEXT))
    p.setColor(QPalette.ColorRole.Highlight,       QColor(_ACCENT))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor("#aaaacc"))
    return p


# ── reusable widgets ──────────────────────────────────────────────────────────

class DotLabel(QLabel):
    """Coloured ● indicator."""
    def set_state(self, installed: bool | None):
        colour = {True: _GREEN, False: _RED, None: _YELLOW}[installed]
        self.setStyleSheet(f"color: {colour}; font-size: 16px;")
        self.setText("●")


class DependencyRow(QWidget):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        self.dot = DotLabel("●")
        self.dot.set_state(None)
        self.dot.setFixedWidth(20)
        row.addWidget(self.dot)
        name = QLabel(label)
        name.setFixedWidth(130)
        row.addWidget(name)
        self.ver = QLabel("")
        self.ver.setStyleSheet("color: #888;")
        row.addWidget(self.ver)
        row.addStretch()

    def update_state(self, installed: bool, version: str = ""):
        self.dot.set_state(installed)
        self.ver.setText(version if installed else "not found")


# ── main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._cfg = config.load()
        self._dark = (self._cfg.get("theme", "dark") == "dark")
        self._thread: QThread | None = None
        self._chat_win: ChatWindow | None = None

        self.setWindowTitle("Claude Code Launcher")
        self.setFixedSize(700, 740)

        self._build_ui()
        self._apply_theme()
        self._refresh_status()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        vbox = QVBoxLayout(root)
        vbox.setSpacing(10)
        vbox.setContentsMargins(0, 0, 0, 12)

        vbox.addWidget(self._make_header())
        vbox.addWidget(self._make_cred_card(), 0, Qt.AlignmentFlag.AlignHCenter)
        vbox.addWidget(self._make_dep_card(),  0, Qt.AlignmentFlag.AlignHCenter)
        vbox.addLayout(self._make_buttons())
        vbox.addWidget(self._make_progress())
        vbox.addWidget(self._make_log_section())

    def _make_header(self) -> QWidget:
        hdr = QFrame()
        hdr.setFixedHeight(58)
        hdr.setObjectName("header")
        lay = QHBoxLayout(hdr)
        lay.setContentsMargins(20, 0, 16, 0)

        title = QLabel("  Claude Code Launcher")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: white;")
        lay.addWidget(title)
        lay.addStretch()

        self._theme_btn = QPushButton("☾")
        self._theme_btn.setFixedSize(36, 36)
        self._theme_btn.setStyleSheet(
            "QPushButton{color:white;background:transparent;border:none;font-size:18px;}"
            "QPushButton:hover{background:rgba(255,255,255,0.15);border-radius:18px;}"
        )
        self._theme_btn.clicked.connect(self._toggle_theme)
        lay.addWidget(self._theme_btn)
        return hdr

    def _make_cred_card(self) -> QGroupBox:
        box = QGroupBox("API Configuration")
        box.setFixedWidth(660)
        form = QVBoxLayout(box)
        form.setSpacing(8)

        # API Key
        kr = QHBoxLayout()
        lbl = QLabel("API Key")
        lbl.setFixedWidth(100)
        kr.addWidget(lbl)
        self._key_edit = QLineEdit(self._cfg.get("api_key", ""))
        self._key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_edit.setPlaceholderText("sk-ant-…")
        self._key_edit.setMinimumWidth(380)
        kr.addWidget(self._key_edit)
        eye = QPushButton("👁")
        eye.setFixedSize(32, 28)
        eye.setCheckable(True)
        eye.setStyleSheet("QPushButton{background:transparent;border:none;font-size:16px;}")
        eye.toggled.connect(
            lambda on: self._key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        kr.addWidget(eye)
        kr.addStretch()
        form.addLayout(kr)

        # Base URL
        ur = QHBoxLayout()
        lbl2 = QLabel("Base URL")
        lbl2.setFixedWidth(100)
        ur.addWidget(lbl2)
        self._url_edit = QLineEdit(self._cfg.get("base_url", ""))
        self._url_edit.setPlaceholderText("https://api.anthropic.com")
        self._url_edit.setMinimumWidth(420)
        ur.addWidget(self._url_edit)
        ur.addStretch()
        form.addLayout(ur)

        # Model
        mr = QHBoxLayout()
        lbl_m = QLabel("Model")
        lbl_m.setFixedWidth(100)
        mr.addWidget(lbl_m)
        self._model_edit = QLineEdit(self._cfg.get("model", ""))
        self._model_edit.setPlaceholderText("留空使用默认claude")
        self._model_edit.setMinimumWidth(420)
        mr.addWidget(self._model_edit)
        mr.addStretch()
        form.addLayout(mr)

        # Working dir
        dr = QHBoxLayout()
        lbl3 = QLabel("Working Dir")
        lbl3.setFixedWidth(100)
        dr.addWidget(lbl3)
        self._dir_edit = QLineEdit(self._cfg.get("working_dir", ""))
        self._dir_edit.setMinimumWidth(330)
        dr.addWidget(self._dir_edit)
        browse = QPushButton("Browse")
        browse.setFixedWidth(70)
        browse.clicked.connect(self._browse_dir)
        dr.addWidget(browse)
        dr.addStretch()
        form.addLayout(dr)

        return box

    def _make_dep_card(self) -> QGroupBox:
        box = QGroupBox("Dependencies")
        box.setFixedWidth(660)
        lay = QVBoxLayout(box)
        lay.setSpacing(4)
        self._row_node   = DependencyRow("Node.js")
        self._row_npm    = DependencyRow("npm")
        self._row_claude = DependencyRow("Claude Code CLI")
        self._row_git    = DependencyRow("Git")
        lay.addWidget(self._row_node)
        lay.addWidget(self._row_npm)
        lay.addWidget(self._row_claude)
        lay.addWidget(self._row_git)
        return box

    def _make_buttons(self) -> QHBoxLayout:
        lay = QHBoxLayout()
        lay.setContentsMargins(20, 0, 20, 0)
        lay.setSpacing(10)

        btn_font = QFont("Segoe UI", 12, QFont.Weight.Medium)

        self._install_btn = QPushButton("Install / Update")
        self._install_btn.setFont(btn_font)
        self._install_btn.setFixedHeight(40)
        self._install_btn.setStyleSheet(
            f"QPushButton{{background:{_ACCENT};color:white;border-radius:6px;padding:0 14px;}}"
            f"QPushButton:hover{{background:#404bc8;}}"
            f"QPushButton:disabled{{background:#444;color:#777;}}"
        )
        self._install_btn.clicked.connect(self._on_install)
        lay.addWidget(self._install_btn)

        self._launch_btn = QPushButton("Launch Claude")
        self._launch_btn.setFont(btn_font)
        self._launch_btn.setFixedHeight(40)
        self._launch_btn.setStyleSheet(
            f"QPushButton{{background:{_GREEN};color:#111;border-radius:6px;padding:0 14px;}}"
            f"QPushButton:hover{{background:#27ae60;}}"
            f"QPushButton:disabled{{background:#444;color:#777;}}"
        )
        self._launch_btn.clicked.connect(self._on_launch)
        lay.addWidget(self._launch_btn)

        self._chat_btn = QPushButton("💬 Chat")
        self._chat_btn.setFont(btn_font)
        self._chat_btn.setFixedHeight(40)
        self._chat_btn.setStyleSheet(
            f"QPushButton{{background:#e67e22;color:white;border-radius:6px;padding:0 14px;}}"
            f"QPushButton:hover{{background:#ca6f1e;}}"
            f"QPushButton:disabled{{background:#444;color:#777;}}"
        )
        self._chat_btn.clicked.connect(self._on_chat)
        lay.addWidget(self._chat_btn)

        self._uninstall_btn = QPushButton("Uninstall")
        self._uninstall_btn.setFont(btn_font)
        self._uninstall_btn.setFixedHeight(40)
        self._uninstall_btn.setStyleSheet(
            f"QPushButton{{background:{_RED};color:white;border-radius:6px;padding:0 14px;}}"
            f"QPushButton:hover{{background:#c0392b;}}"
            f"QPushButton:disabled{{background:#444;color:#777;}}"
        )
        self._uninstall_btn.clicked.connect(self._on_uninstall)
        lay.addWidget(self._uninstall_btn)

        refresh = QPushButton("Check Status")
        refresh.setFont(btn_font)
        refresh.setFixedHeight(40)
        refresh.setStyleSheet(
            "QPushButton{background:transparent;border:1.5px solid #666;border-radius:6px;padding:0 14px;}"
            "QPushButton:hover{background:rgba(128,128,128,0.2);}"
        )
        refresh.clicked.connect(self._refresh_status)
        lay.addWidget(refresh)

        lay.addStretch()
        return lay

    def _make_progress(self) -> QProgressBar:
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        self._progress.setContentsMargins(20, 0, 20, 0)
        self._progress.setStyleSheet(
            f"QProgressBar{{border:none;background:#333;border-radius:3px;margin:0 20px;}}"
            f"QProgressBar::chunk{{background:{_ACCENT};border-radius:3px;}}"
        )
        return self._progress

    def _make_log_section(self) -> QWidget:
        w = QWidget()
        vlay = QVBoxLayout(w)
        vlay.setContentsMargins(20, 0, 20, 0)
        vlay.setSpacing(4)

        hdr = QHBoxLayout()
        log_lbl = QLabel("Log")
        log_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        hdr.addWidget(log_lbl)
        hdr.addStretch()
        clear_btn = QPushButton("Clear")
        clear_btn.setFixedSize(55, 24)
        clear_btn.setStyleSheet(
            "QPushButton{background:transparent;border:1px solid #666;border-radius:4px;font-size:11px;}"
            "QPushButton:hover{background:rgba(128,128,128,0.2);}"
        )
        clear_btn.clicked.connect(lambda: self._log.clear())
        hdr.addWidget(clear_btn)
        vlay.addLayout(hdr)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Consolas", 10))
        self._log.setMinimumHeight(160)
        vlay.addWidget(self._log)
        return w

    # ── theme ─────────────────────────────────────────────────────────────────

    def _apply_theme(self):
        qapp = QApplication.instance()
        qapp.setPalette(_dark_palette() if self._dark else _light_palette())
        hdr_color = _ACCENT
        self.findChild(QFrame, "header").setStyleSheet(
            f"QFrame#header{{background:{hdr_color};}}"
        )

    def _toggle_theme(self):
        self._dark = not self._dark
        self._apply_theme()
        self._cfg["theme"] = "dark" if self._dark else "light"
        config.save(self._cfg)

    # ── status refresh ────────────────────────────────────────────────────────

    def _refresh_status(self):
        status = installer.get_install_status()
        self._apply_status(status)

    def _apply_status(self, status: dict):
        self._row_node.update_state(status["node"]["installed"],   status["node"]["version"])
        self._row_npm.update_state(status["npm"]["installed"],     status["npm"]["version"])
        self._row_claude.update_state(status["claude"]["installed"], status["claude"]["version"])
        self._row_git.update_state(status["git"]["installed"],    status["git"]["version"])

    # ── worker management ─────────────────────────────────────────────────────

    def _run_worker(self, task, on_start=None, on_done=None):
        if on_start:
            on_start()
        self._set_busy(True)
        self._progress.setValue(5)
        self._log.clear()

        worker = Worker(task)
        thread = QThread()
        worker.moveToThread(thread)

        _steps = [10, 30, 60, 90]
        _idx = [0]

        def _on_progress(msg):
            self._log.appendPlainText(msg)
            if _idx[0] < len(_steps):
                self._progress.setValue(_steps[_idx[0]])
                _idx[0] += 1

        def _on_done(ok, msg):
            self._progress.setValue(100 if ok else 0)
            prefix = "✓" if ok else "✗"
            self._log.appendPlainText(f"{prefix} {msg}")
            self._set_busy(False)
            self._refresh_status()
            thread.quit()
            if on_done:
                on_done(ok, msg)

        worker.progress.connect(_on_progress)
        worker.finished.connect(_on_done)
        thread.started.connect(worker.run)
        thread.start()

        self._thread = thread
        self._worker = worker  # keep references alive

    def _set_busy(self, busy: bool):
        for btn in (self._install_btn, self._launch_btn, self._uninstall_btn, self._chat_btn):
            btn.setEnabled(not busy)

    # ── install ───────────────────────────────────────────────────────────────

    def _on_install(self):
        self._save_cfg()
        self._run_worker(installer.full_install)

    # ── launch ────────────────────────────────────────────────────────────────

    def _on_launch(self):
        api_key = self._key_edit.text().strip()
        base_url = self._url_edit.text().strip()
        model = self._model_edit.text().strip()
        working_dir = self._dir_edit.text().strip()

        if not api_key:
            self._log.appendPlainText("✗ API Key is required.")
            return

        claude_ok, _ = installer.check_claude()
        if not claude_ok:
            self._log.appendPlainText("✗ Claude Code is not installed. Run Install first.")
            return

        self._save_cfg()
        try:
            installer.launch_claude(api_key, base_url, working_dir, model)
            self._log.appendPlainText("✓ Claude Code launched in a new terminal window.")
        except Exception as e:
            self._log.appendPlainText(f"✗ Launch failed: {e}")

    # ── chat ──────────────────────────────────────────────────────────────────

    def _on_chat(self):
        api_key = self._key_edit.text().strip()
        if not api_key:
            self._log.appendPlainText("✗ API Key is required.")
            return
        base_url = self._url_edit.text().strip() or "https://api.anthropic.com"
        self._save_cfg()
        if self._chat_win is None or not self._chat_win.isVisible():
            self._chat_win = ChatWindow(api_key, base_url, dark=self._dark)
        self._chat_win.show()
        self._chat_win.raise_()
        self._chat_win.activateWindow()

    # ── uninstall ─────────────────────────────────────────────────────────────

    def _on_uninstall(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Uninstall Components")
        dlg.setFixedWidth(360)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Select components to uninstall:"))

        cb_claude = QCheckBox("Claude Code CLI + config files")
        cb_claude.setChecked(True)
        cb_node = QCheckBox("Node.js && npm")
        cb_git = QCheckBox("Git")
        for cb in (cb_claude, cb_node, cb_git):
            layout.addWidget(cb)

        layout.addWidget(QLabel("<small>Node.js / Git uninstall requires admin (UAC prompt will appear).</small>"))

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("Uninstall")
        ok_btn.setStyleSheet(f"QPushButton{{background:{_RED};color:white;border-radius:4px;padding:4px 16px;}}")
        cancel_btn = QPushButton("Cancel")
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        ok_btn.clicked.connect(dlg.accept)
        cancel_btn.clicked.connect(dlg.reject)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        components = []
        if cb_claude.isChecked():
            components.append("claude")
        if cb_node.isChecked():
            components.append("node")
        if cb_git.isChecked():
            components.append("git")

        if not components:
            return

        self._run_worker(lambda progress_cb: installer.full_uninstall(components, progress_cb))

    # ── helpers ───────────────────────────────────────────────────────────────

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Working Directory",
                                             self._dir_edit.text())
        if d:
            self._dir_edit.setText(d)

    def _save_cfg(self):
        self._cfg.update({
            "api_key":     self._key_edit.text().strip(),
            "base_url":    self._url_edit.text().strip(),
            "model":       self._model_edit.text().strip(),
            "working_dir": self._dir_edit.text().strip(),
        })
        config.save(self._cfg)
