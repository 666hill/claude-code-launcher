"""
Persists user configuration (API key, base URL, working dir) to disk.
Config is stored in ~/.claude_launcher/config.json.
"""
import json
from pathlib import Path


CONFIG_DIR = Path.home() / ".claude_launcher"
CONFIG_FILE = CONFIG_DIR / "config.json"

_DEFAULTS = {
    "api_key": "",
    "base_url": "https://api.anthropic.com",
    "working_dir": str(Path.home()),
    "theme": "dark",
}


def load() -> dict:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return {**_DEFAULTS, **data}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULTS)


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def mask_key(key: str) -> str:
    """Return a masked version for display, e.g. sk-ant-****XXXX."""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:6] + "****" + key[-4:]
