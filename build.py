"""
Build a standalone Windows executable with PyInstaller.
Usage: python build.py
"""
import subprocess
import sys

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--windowed",
    "--name", "ClaudeCodeLauncher",
    "--add-data", "src;src",
    "--icon", "app.ico",
    "main.py",
]

print("Running:", " ".join(cmd))
subprocess.run(cmd, check=True)
print("\nDone. Executable is in dist/ClaudeCodeLauncher.exe")
