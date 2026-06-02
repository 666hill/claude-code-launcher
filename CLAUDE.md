# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A PyQt6 desktop GUI for Windows that installs, configures, and launches the Claude Code CLI, plus a built-in streaming chat window that calls the Anthropic Messages API directly (bypasses IME issues the CLI has on Windows). Packaged as a single `.exe` via PyInstaller.

## Build & run

```bash
# Install deps into the project's virtual environment
pip install -r requirements.txt

# Run the app during development
python main.py

# Build a standalone Windows .exe
python build.py
# Output: dist/ClaudeCodeLauncher.exe
```

## Architecture

```
main.py                  ← entry point: QApplication + MainWindow
src/
  gui.py                 ← MainWindow: install/launch/chat/uninstall UI, dark/light theme
  chat.py                ← ChatWindow: streaming Anthropic /v1/messages client, markdown rendering
  config.py              ← read/write ~/.claude_launcher/config.json (api_key, base_url, model, theme, working_dir)
  installer.py           ← detect, download, install (winget/MSI), and uninstall Node.js, Git, and Claude Code CLI
```

### Key design decisions

- **Chat IME fix**: The chat window in `chat.py` calls the HTTP API directly (`urllib` + SSE streaming) rather than spawning `claude` CLI. This was done because the CLI had Chinese IME input problems on Windows.
- **Environment bridging**: `installer.py:launch_claude()` persists API credentials to the Windows registry (`HKCU\Environment`) so every new terminal inherits them — avoids shell startup script complexity.
- **Silent installs**: Node.js and Git are installed non-interactively via winget (primary) with MSI/NSIS fallback, using PowerShell elevation (`Start-Process -Verb RunAs`).
- **Config location**: `~/.claude_launcher/config.json` — separate from Claude Code's own `~/.claude.json`.
- **Streaming**: `StreamWorker` runs on a `QThread`, emits `token` signals for real-time UI updates. Model list is hardcoded in `chat.py:MODELS`.

### Data flow

1. User enters API key/base URL/model/working dir in the GUI → saved via `config.py`
2. "Install/Update" checks and installs Node.js → npm → Claude Code CLI → patches onboarding → sets PS execution policy
3. "Launch Claude" sets env vars (registry on Win) → opens `claude` in a new terminal tab (Windows Terminal preferred, PowerShell fallback)
4. "Chat" opens an embedded streaming chat window that calls the Anthropic API over HTTPS
