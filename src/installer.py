"""
Handles detection, installation, and uninstallation of Node.js, npm, and Claude Code CLI.
"""
import subprocess
import os
import json
import platform
import shutil   
import urllib.request
import ssl
import threading
from pathlib import Path


def _ssl_context():
    """Unverified SSL context for environments where system certs are missing (e.g. packed exe)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _download(url: str, dest: Path, progress_cb=None, label: str = "Downloading",
              retries: int = 3) -> None:
    """Download url → dest with progress, bypassing SSL verification. Retries on failure."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            if attempt > 1 and progress_cb:
                progress_cb(f"{label}... retrying (attempt {attempt}/{retries})")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=_ssl_context(), timeout=60) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk = 65536
                with open(dest, "wb") as f:
                    while True:
                        block = resp.read(chunk)
                        if not block:
                            break
                        f.write(block)
                        downloaded += len(block)
                        if progress_cb and total:
                            pct = min(100, int(downloaded * 100 / total))
                            progress_cb(f"{label}... {pct}%")
            return  # success
        except Exception as e:
            last_err = e
            dest.unlink(missing_ok=True)
    raise RuntimeError(f"Download failed after {retries} attempts: {last_err}")


NODE_WINDOWS_URL = "https://nodejs.org/dist/v20.19.0/node-v20.19.0-x64.msi"
NODE_MAC_URL = "https://nodejs.org/dist/v20.19.0/node-v20.19.0.pkg"
GIT_WINDOWS_URL = "https://github.com/git-for-windows/git/releases/download/v2.45.0.windows.1/Git-2.45.0-64-bit.exe"

CLAUDE_JSON = Path.home() / ".claude.json"
CLAUDE_DIR = Path.home() / ".claude"
LAUNCHER_DIR = Path.home() / ".claude_launcher"

# Always-present Windows system paths that packed exes may strip from PATH
_WIN_SYSTEM_DIRS = [
    os.environ.get("SystemRoot", r"C:\Windows") + r"\System32",
    os.environ.get("SystemRoot", r"C:\Windows"),
    os.environ.get("SystemRoot", r"C:\Windows") + r"\System32\Wbem",
]


def _win_env() -> dict | None:
    """Return an env dict guaranteed to include Windows System32 dirs."""
    if platform.system() != "Windows":
        return None
    env = os.environ.copy()
    path_parts = env.get("PATH", "").split(";")
    for d in _WIN_SYSTEM_DIRS:
        if d.lower() not in [p.lower() for p in path_parts]:
            path_parts.insert(0, d)
    env["PATH"] = ";".join(path_parts)
    return env


def run_cmd(cmd: list[str], capture=True) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            shell=(platform.system() == "Windows"),
            env=_win_env(),
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return 1, "", f"Command not found: {cmd[0]}"


def _refresh_windows_path() -> None:
    """Re-read PATH from registry so newly installed tools are visible."""
    if platform.system() != "Windows":
        return
    try:
        import winreg
        parts: list[str] = []
        for hive, sub in [
            (winreg.HKEY_LOCAL_MACHINE,
             r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            (winreg.HKEY_CURRENT_USER, r"Environment"),
        ]:
            try:
                with winreg.OpenKey(hive, sub) as k:
                    val, _ = winreg.QueryValueEx(k, "Path")
                    parts.append(val)
            except OSError:
                pass
        if parts:
            os.environ["PATH"] = ";".join(parts)
    except ImportError:
        pass


def check_node() -> tuple[bool, str]:
    code, out, _ = run_cmd(["node", "--version"])
    return code == 0, out


def check_npm() -> tuple[bool, str]:
    code, out, _ = run_cmd(["npm", "--version"])
    return code == 0, out


def check_claude() -> tuple[bool, str]:
    code, out, _ = run_cmd(["claude", "--version"])
    return code == 0, out


def check_git() -> tuple[bool, str]:
    code, out, _ = run_cmd(["git", "--version"])
    return code == 0, out


def get_install_status() -> dict:
    node_ok, node_ver = check_node()
    npm_ok, npm_ver = check_npm()
    claude_ok, claude_ver = check_claude()
    git_ok, git_ver = check_git()
    return {
        "node":   {"installed": node_ok,   "version": node_ver},
        "npm":    {"installed": npm_ok,    "version": npm_ver},
        "claude": {"installed": claude_ok, "version": claude_ver},
        "git":    {"installed": git_ok,    "version": git_ver},
    }


# ── onboarding patch ──────────────────────────────────────────────────────────

def patch_onboarding() -> None:
    """Write hasCompletedOnboarding:true into ~/.claude.json."""
    data = {}
    if CLAUDE_JSON.exists():
        try:
            data = json.loads(CLAUDE_JSON.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    data["hasCompletedOnboarding"] = True
    CLAUDE_JSON.parent.mkdir(parents=True, exist_ok=True)
    CLAUDE_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── installation ──────────────────────────────────────────────────────────────

def _install_node_winget(progress_cb=None) -> bool:
    """Install Node.js LTS via winget (Win10 1809+). Returns True on success."""
    rc, _, _ = run_cmd(["winget", "--version"])
    if rc != 0:
        return False
    if progress_cb:
        progress_cb("Installing Node.js via winget (UAC prompt may appear)...")
    ps = _find_powershell()
    ps_script = (
        "$p = Start-Process -FilePath winget "
        "-ArgumentList 'install --id OpenJS.NodeJS.LTS --silent "
        "--accept-package-agreements --accept-source-agreements' "
        "-Verb RunAs -Wait -PassThru; exit $p.ExitCode"
    )
    try:
        result = subprocess.run(
            [ps, "-ExecutionPolicy", "Bypass", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, env=_win_env(),
        )
        code = result.returncode
    except Exception as e:
        if progress_cb:
            progress_cb(f"winget launch failed: {e}. Trying MSI...")
        return False

    if code == 0:
        _refresh_windows_path()
        return True
    if progress_cb:
        progress_cb(f"winget failed (code {code}). Trying MSI...")
    return False


def _install_node_msi(progress_cb=None) -> bool:
    """Install Node.js via MSI download. Falls back when winget isn't available."""
    temp = Path(os.environ.get("TEMP", "."))
    msi_path = temp / "node_installer.msi"
    log_path = temp / "node_install.log"

    if progress_cb:
        progress_cb("Downloading Node.js installer...")
    try:
        _download(NODE_WINDOWS_URL, msi_path, progress_cb, "Downloading Node.js")
    except Exception as e:
        if progress_cb:
            progress_cb(f"Download failed: {e}")
        return False

    if msi_path.stat().st_size < 1_000_000:
        if progress_cb:
            progress_cb("Download error: file too small, likely incomplete.")
        msi_path.unlink(missing_ok=True)
        return False

    if progress_cb:
        progress_cb("Installing Node.js (this may take a minute)...")

    msiexec = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "msiexec.exe")
    ps_script = (
        f"$p = Start-Process -FilePath '{msiexec}' "
        f"-ArgumentList '/i \"{msi_path}\" /quiet /norestart /log \"{log_path}\"' "
        f"-Verb RunAs -Wait -PassThru; exit $p.ExitCode"
    )
    ps = _find_powershell()
    code, out, err = run_cmd([ps, "-ExecutionPolicy", "Bypass", "-NoProfile", "-Command", ps_script])

    msi_path.unlink(missing_ok=True)

    if code != 0 and progress_cb:
        detail = (err or out).strip()
        if not detail and log_path.exists():
            try:
                lines = log_path.read_text(encoding="utf-16", errors="replace").splitlines()
                detail = "\n".join(ln for ln in lines[-15:] if ln.strip())
            except Exception:
                pass
        progress_cb(f"Node.js MSI error (code {code}):\n{detail or '(no details)'}")

    log_path.unlink(missing_ok=True)
    _refresh_windows_path()
    return code == 0


def install_node_windows(progress_cb=None) -> bool:
    return _install_node_winget(progress_cb) or _install_node_msi(progress_cb)


def install_git_windows(progress_cb=None) -> bool:
    exe_path = Path(os.environ.get("TEMP", ".")) / "git_installer.exe"
    if progress_cb:
        progress_cb("Downloading Git installer...")
    try:
        _download(GIT_WINDOWS_URL, exe_path, progress_cb, "Downloading Git")
    except Exception as e:
        if progress_cb:
            progress_cb(f"Git download failed: {e}")
        return False

    if progress_cb:
        progress_cb("Installing Git (silent)...")
    code, _, err = run_cmd([
        str(exe_path),
        "/VERYSILENT", "/NORESTART", "/NOCANCEL", "/SP-",
        "/COMPONENTS=icons,ext\\reg\\shellhere,assoc,assoc_sh",
    ])
    exe_path.unlink(missing_ok=True)
    if code != 0 and progress_cb:
        progress_cb(f"Git install error: {err}")
    _refresh_windows_path()
    return code == 0


def install_node_mac(progress_cb=None) -> bool:
    pkg_path = Path("/tmp/node_installer.pkg")
    if progress_cb:
        progress_cb("Downloading Node.js installer...")
    try:
        _download(NODE_MAC_URL, pkg_path, progress_cb, "Downloading Node.js")
    except Exception as e:
        if progress_cb:
            progress_cb(f"Download failed: {e}")
        return False

    if progress_cb:
        progress_cb("Installing Node.js...")
    code, _, _ = run_cmd(["sudo", "installer", "-pkg", str(pkg_path), "-target", "/"])
    pkg_path.unlink(missing_ok=True)
    return code == 0


def install_claude_code(progress_cb=None) -> tuple[bool, str]:
    if progress_cb:
        progress_cb("Installing Claude Code via npm...")
    code, out, err = run_cmd(["npm", "install", "-g", "@anthropic-ai/claude-code"])
    if code == 0:
        return True, "Claude Code installed successfully."
    return False, err or out


def _fix_powershell_execution_policy(progress_cb=None) -> None:
    """Set CurrentUser PS execution policy to RemoteSigned so npm .ps1 scripts run."""
    ps = _find_powershell()
    try:
        result = subprocess.run(
            [ps, "-ExecutionPolicy", "Bypass", "-NoProfile", "-Command",
             "Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force"],
            capture_output=True, text=True, env=_win_env(),
        )
        # PS returns non-zero when a higher-scope policy overrides the setting,
        # but the output confirms it was actually applied — treat that as success.
        output = (result.stdout + result.stderr)
        succeeded = result.returncode == 0 or "成功" in output or "successfully" in output.lower()
        if progress_cb:
            if succeeded:
                progress_cb("PowerShell execution policy set to RemoteSigned.")
            else:
                progress_cb(f"Could not set execution policy (non-critical): {result.stderr.strip()}")
    except Exception as e:
        if progress_cb:
            progress_cb(f"Could not set execution policy (non-critical): {e}")


def full_install(progress_cb=None) -> tuple[bool, str]:
    system = platform.system()

    # ── Git ──────────────────────────────────────────────────────────────────
    git_ok, _ = check_git()
    if not git_ok:
        if system == "Windows":
            ok = install_git_windows(progress_cb)
            if not ok:
                return False, "Failed to install Git. Please install it manually."
        else:
            if progress_cb:
                progress_cb("Git not found. Please install Git manually.")

    # ── Node.js ───────────────────────────────────────────────────────────────
    node_ok, _ = check_node()
    if not node_ok:
        if system == "Windows":
            ok = install_node_windows(progress_cb)
        elif system == "Darwin":
            ok = install_node_mac(progress_cb)
        else:
            return False, (
                "Auto-install on Linux is not supported.\n"
                "Please install Node.js manually: https://nodejs.org"
            )
        if not ok:
            return False, "Failed to install Node.js. Please install it manually."

    npm_ok, _ = check_npm()
    if not npm_ok:
        return False, "npm not found after Node.js install. Please restart and try again."

    # ── Claude Code ───────────────────────────────────────────────────────────
    ok, msg = install_claude_code(progress_cb)
    if not ok:
        return False, msg

    _refresh_windows_path()

    if progress_cb:
        progress_cb("Patching onboarding config...")
    patch_onboarding()

    if system == "Windows":
        _fix_powershell_execution_policy(progress_cb)

    return True, "All dependencies installed successfully."


def run_full_install_async(progress_cb, done_cb):
    def _worker():
        ok, msg = full_install(progress_cb)
        done_cb(ok, msg)

    threading.Thread(target=_worker, daemon=True).start()


# ── uninstallation ────────────────────────────────────────────────────────────

def _remove_path(p: Path, progress_cb=None):
    def _force_remove(func, path, *_):
        try:
            os.chmod(path, 0o777)
            func(path)
        except Exception:
            pass

    try:
        if p.is_dir():
            shutil.rmtree(p, onexc=_force_remove)
        elif p.exists():
            p.unlink()
        if progress_cb:
            progress_cb(f"Removed: {p}")
    except Exception as e:
        if progress_cb:
            progress_cb(f"Could not remove {p}: {e}")


def clean_claude_files(progress_cb=None) -> None:
    targets = [CLAUDE_JSON, CLAUDE_DIR, LAUNCHER_DIR]

    if platform.system() == "Windows":
        npm_bin = Path(os.environ.get("APPDATA", "")) / "npm"
        targets += [npm_bin / "claude", npm_bin / "claude.cmd"]

    for t in targets:
        _remove_path(t, progress_cb)


def clean_registry(progress_cb=None) -> None:
    """Remove Claude-related Windows registry uninstall entries."""
    if platform.system() != "Windows":
        return
    try:
        import winreg
    except ImportError:
        return

    uninstall_paths = [
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]

    def _scan_and_delete(hive, path):
        try:
            with winreg.OpenKey(hive, path) as base:
                count = winreg.QueryInfoKey(base)[0]
                subkeys = [winreg.EnumKey(base, i) for i in range(count)]
        except OSError:
            return

        for name in subkeys:
            full = path + "\\" + name
            try:
                with winreg.OpenKey(hive, full) as k:
                    display = ""
                    try:
                        display, _ = winreg.QueryValueEx(k, "DisplayName")
                    except OSError:
                        pass
                if "claude" in display.lower():
                    winreg.DeleteKey(hive, full)
                    if progress_cb:
                        progress_cb(f"Deleted registry key: {full}")
            except OSError as e:
                if progress_cb:
                    progress_cb(f"Registry skip {full}: {e}")

    for hive, path in uninstall_paths:
        _scan_and_delete(hive, path)


def uninstall_claude_code(progress_cb=None) -> tuple[bool, str]:
    npm_ok, _ = check_npm()
    if npm_ok:
        if progress_cb:
            progress_cb("Uninstalling Claude Code via npm...")
        code, _, err = run_cmd(["npm", "uninstall", "-g", "@anthropic-ai/claude-code"])
        if code != 0 and progress_cb:
            progress_cb(f"npm uninstall warning: {err}")
    else:
        if progress_cb:
            progress_cb("npm not found, skipping npm uninstall...")

    if progress_cb:
        progress_cb("Removing Claude config files...")
    clean_claude_files(progress_cb)

    if progress_cb:
        progress_cb("Cleaning registry entries...")
    clean_registry(progress_cb)

    return True, "Claude Code uninstalled and cleaned up."


def _find_powershell() -> str:
    """Return absolute path to powershell.exe, works even when PATH is stripped."""
    # shutil.which may fail in packed-exe environments where PATH is minimal
    candidate = shutil.which("powershell") or shutil.which("powershell.exe")
    if candidate:
        return candidate
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    for rel in [r"System32\WindowsPowerShell\v1.0\powershell.exe",
                r"SysWOW64\WindowsPowerShell\v1.0\powershell.exe"]:
        abs_path = os.path.join(system_root, rel)
        if os.path.isfile(abs_path):
            return abs_path
    return "powershell.exe"  # last resort, let OS resolve it


def _run_elevated(exe: str, args: str, progress_cb=None) -> bool:
    """Run exe with args elevated via PowerShell Start-Process -Verb RunAs -Wait."""
    ps = _find_powershell()
    exe_escaped = exe.replace("'", "''")
    args_escaped = args.replace("'", "''")
    ps_script = (
        f"$p = Start-Process -FilePath '{exe_escaped}' "
        f"-ArgumentList '{args_escaped}' "
        f"-Verb RunAs -Wait -PassThru; exit $p.ExitCode"
    )
    try:
        result = subprocess.run(
            [ps, "-ExecutionPolicy", "Bypass", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, env=_win_env(),
        )
        if progress_cb:
            progress_cb(f"Uninstaller exit code: {result.returncode}")
        return result.returncode == 0
    except Exception as e:
        if progress_cb:
            progress_cb(f"Uninstaller failed: {e}")
        return False


def _uninstall_via_winget(pkg_id: str, progress_cb=None) -> bool:
    """Try to uninstall a package via winget with UAC elevation. Returns True on success."""
    rc, _, _ = run_cmd(["winget", "--version"])
    if rc != 0:
        return False
    if progress_cb:
        progress_cb(f"Uninstalling {pkg_id} via winget...")
    ps = _find_powershell()
    ps_script = (
        f"$p = Start-Process -FilePath winget "
        f"-ArgumentList 'uninstall --id {pkg_id} --silent --accept-source-agreements' "
        f"-Verb RunAs -Wait -PassThru; exit $p.ExitCode"
    )
    try:
        result = subprocess.run(
            [ps, "-ExecutionPolicy", "Bypass", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, env=_win_env(),
        )
        return result.returncode == 0
    except Exception:
        return False


def _uninstall_via_registry(keyword: str, progress_cb=None) -> bool:
    """Find an installed product by display name keyword and run its uninstaller."""
    if platform.system() != "Windows":
        return False
    try:
        import winreg
    except ImportError:
        return False

    uninstall_paths = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]

    for hive, base_path in uninstall_paths:
        try:
            with winreg.OpenKey(hive, base_path) as base:
                count = winreg.QueryInfoKey(base)[0]
                subkeys = [winreg.EnumKey(base, i) for i in range(count)]
        except OSError:
            continue

        for name in subkeys:
            full = base_path + "\\" + name
            try:
                with winreg.OpenKey(hive, full) as k:
                    try:
                        display, _ = winreg.QueryValueEx(k, "DisplayName")
                    except OSError:
                        continue
                    if keyword.lower() not in display.lower():
                        continue
                    try:
                        uninstall_cmd, _ = winreg.QueryValueEx(k, "QuietUninstallString")
                    except OSError:
                        try:
                            uninstall_cmd, _ = winreg.QueryValueEx(k, "UninstallString")
                        except OSError:
                            continue

                    if progress_cb:
                        progress_cb(f"Found {display}, running uninstaller...")

                    uninstall_cmd = uninstall_cmd.strip()
                    if uninstall_cmd.startswith('"'):
                        end_quote = uninstall_cmd.find('"', 1)
                        exe = uninstall_cmd[1:end_quote]
                        args = uninstall_cmd[end_quote + 1:].strip()
                    else:
                        parts = uninstall_cmd.split(None, 1)
                        exe = parts[0]
                        args = parts[1] if len(parts) > 1 else ""

                    exe_lower = exe.lower()
                    if "msiexec" in exe_lower:
                        # MSI uninstall: switch /I (install) to /X (remove), use msiexec-style flags
                        args = args.replace("/I", "/X").replace("/i", "/X")
                        if "/quiet" not in args.lower():
                            args = (args + " /quiet /norestart").strip()
                    else:
                        # NSIS / Inno Setup uninstallers use /SILENT or /VERYSILENT
                        if "/SILENT" not in args.upper() and "/VERYSILENT" not in args.upper():
                            args = (args + " /SILENT").strip()

                    return _run_elevated(exe, args, progress_cb)
            except OSError:
                continue
    return False


def uninstall_node_windows(progress_cb=None) -> tuple[bool, str]:
    if progress_cb:
        progress_cb("Uninstalling Node.js...")
    node_present, _ = check_node()
    if not node_present:
        if progress_cb:
            progress_cb("Node.js is not installed, skipping.")
        return True, "Node.js was not installed."
    ok = _uninstall_via_winget("OpenJS.NodeJS.LTS", progress_cb)
    if not ok:
        ok = _uninstall_via_winget("OpenJS.NodeJS", progress_cb)
    if not ok:
        if progress_cb:
            progress_cb("winget not available, trying registry uninstaller...")
        ok = _uninstall_via_registry("node.js", progress_cb)
    _refresh_windows_path()
    if ok:
        return True, "Node.js uninstalled successfully."
    return False, "Could not uninstall Node.js automatically. Please uninstall manually via Control Panel."


def uninstall_git_windows(progress_cb=None) -> tuple[bool, str]:
    if progress_cb:
        progress_cb("Uninstalling Git...")
    git_present, _ = check_git()
    if not git_present:
        if progress_cb:
            progress_cb("Git is not installed, skipping.")
        return True, "Git was not installed."
    ok = _uninstall_via_winget("Git.Git", progress_cb)
    if not ok:
        if progress_cb:
            progress_cb("winget not available, trying registry uninstaller...")
        ok = _uninstall_via_registry("git", progress_cb)
    _refresh_windows_path()
    if ok:
        return True, "Git uninstalled successfully."
    return False, "Could not uninstall Git automatically. Please uninstall manually via Control Panel."


def full_uninstall(components: list[str], progress_cb=None) -> tuple[bool, str]:
    """Uninstall selected components. components is a subset of ['claude', 'node', 'git']."""
    errors = []
    system = platform.system()

    if "claude" in components:
        ok, msg = uninstall_claude_code(progress_cb)
        if not ok:
            errors.append(msg)

    if "node" in components:
        if system == "Windows":
            ok, msg = uninstall_node_windows(progress_cb)
        else:
            ok, msg = False, "Node.js auto-uninstall is only supported on Windows."
        if not ok:
            errors.append(msg)

    if "git" in components:
        if system == "Windows":
            ok, msg = uninstall_git_windows(progress_cb)
        else:
            ok, msg = False, "Git auto-uninstall is only supported on Windows."
        if not ok:
            errors.append(msg)

    if errors:
        return False, "\n".join(errors)
    return True, "Selected components uninstalled successfully."


def run_uninstall_async(progress_cb, done_cb, components: list[str] = None):
    if components is None:
        components = ["claude"]

    def _worker():
        ok, msg = full_uninstall(components, progress_cb)
        done_cb(ok, msg)

    threading.Thread(target=_worker, daemon=True).start()


# ── launch ────────────────────────────────────────────────────────────────────

def _persist_windows_env(name: str, value: str) -> None:
    """Write env var to HKCU\\Environment so all new terminals see it."""
    try:
        import winreg, ctypes
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", access=winreg.KEY_SET_VALUE
        ) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        # Broadcast so Explorer/shell picks up the change immediately
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, "Environment", 2, 2000, None
        )
    except Exception:
        pass


def launch_claude(api_key: str, base_url: str, working_dir: str = None, model: str = None) -> subprocess.Popen:
    cwd = working_dir or str(Path.home())
    system = platform.system()

    if system == "Windows":
        _persist_windows_env("ANTHROPIC_API_KEY", api_key)
        if base_url:
            _persist_windows_env("ANTHROPIC_BASE_URL", base_url)
        if model:
            _persist_windows_env("ANTHROPIC_MODEL", model)

        os.environ["ANTHROPIC_API_KEY"] = api_key
        if base_url:
            os.environ["ANTHROPIC_BASE_URL"] = base_url
        if model:
            os.environ["ANTHROPIC_MODEL"] = model

        # Try Windows Terminal — open an interactive shell that just runs `claude`.
        # Env vars are already in the environment so no shell juggling needed.
        # This matches "user opens Windows Terminal and types claude" exactly.
        wt = shutil.which("wt") or shutil.which("wt.exe")
        if wt:
            return subprocess.Popen(
                [wt, "new-tab",
                 "--startingDirectory", cwd,
                 "--title", "Claude Code",
                 "powershell.exe", "-ExecutionPolicy", "Bypass",
                 "-NoExit", "-Command", "claude"],
            )

        # Fallback: standalone PowerShell window
        ps = _find_powershell()
        if ps:
            return subprocess.Popen(
                [ps, "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", "claude"],
                cwd=cwd,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )

        # Last resort: cmd.exe — no execution policy issue here
        return subprocess.Popen(
            ["cmd", "/k", "chcp 65001 > nul & claude"],
            cwd=cwd,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    elif system == "Darwin":
        exports = f"export ANTHROPIC_API_KEY='{api_key}'"
        if base_url:
            exports += f"; export ANTHROPIC_BASE_URL='{base_url}'"
        script = f'tell application "Terminal" to do script "{exports}; claude"'
        return subprocess.Popen(["osascript", "-e", script])
    else:
        exports = f"export ANTHROPIC_API_KEY='{api_key}'"
        if base_url:
            exports += f"; export ANTHROPIC_BASE_URL='{base_url}'"
        for term in ["gnome-terminal", "xterm", "konsole"]:
            rc, _, _ = run_cmd(["which", term])
            if rc == 0:
                return subprocess.Popen(
                    [term, "--", "bash", "-c", f"{exports}; claude; bash"],
                    cwd=cwd,
                )
        raise RuntimeError("No supported terminal emulator found.")
