"""Run the system folder dialog in its own GUI process, outside HTTP threads."""

import json
import os
import subprocess
import sys


def choose(initial):
    result = subprocess.run(
        [sys.executable, "-m", "nexus.folder_picker", str(initial)],
        capture_output=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        raise OSError("无法打开系统文件夹选择窗口，请检查 Python 的 Tk 支持。"
                      + ("\n" + result.stderr.strip() if result.stderr.strip() else ""))
    return json.loads(result.stdout)


if __name__ == "__main__":
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        folder = filedialog.askdirectory(
            parent=root, title="选择项目文件夹", initialdir=sys.argv[1], mustexist=True,
        )
        print(json.dumps({"path": folder or None}))
    finally:
        root.destroy()
