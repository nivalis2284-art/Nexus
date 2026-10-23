import sys
from types import SimpleNamespace

import pytest

from nexus.tools import shell


def test_quotes_and_inline_option_preserve_argument_boundaries():
    assert shell._argv("python -c 'print(1)'") == ["python", "-c", "print(1)"]
    assert shell._argv(r'program --target="C:\Program Files\x"') == [
        "program", r"--target=C:\Program Files\x"]
    assert shell._argv(r'program "" "a\\\"b"') == ["program", "", 'a\\"b']
    assert shell._argv(r'program C:\work\file.txt') == ["program", r"C:\work\file.txt"]
    with pytest.raises(ValueError, match="unclosed"):
        shell._argv("program 'unfinished")


def test_single_quoted_python_command_executes(tmp_path):
    value = shell.bash(SimpleNamespace(root=tmp_path),
                       {"command": f'"{sys.executable}" -c \'print(1)\'', "timeout": 10.0})
    assert value["ok"]
    assert "stdout:\n1" in value["content"]


def test_native_and_utf8_chinese_output_are_readable(monkeypatch):
    monkeypatch.setattr(shell.locale, "getencoding", lambda: "cp936")
    # A UTF-8 console setting is independent of a native subprocess's bytes.
    if sys.platform == "win32":
        import ctypes
        monkeypatch.setattr(ctypes.windll.kernel32, "GetConsoleOutputCP", lambda: 936)
    assert shell._decode_output("中文路径".encode("cp936")) == "中文路径"
    assert shell._decode_output("中文路径".encode("utf-8")) == "中文路径"


def test_native_encoded_subprocess_output(tmp_path, monkeypatch):
    monkeypatch.setattr(shell.locale, "getencoding", lambda: "cp936")
    if sys.platform == "win32":
        import ctypes
        monkeypatch.setattr(ctypes.windll.kernel32, "GetConsoleOutputCP", lambda: 65001)
    command = f'"{sys.executable}" -c \'import sys; sys.stdout.buffer.write(bytes.fromhex("d6d0cec4"))\''
    value = shell.bash(SimpleNamespace(root=tmp_path), {"command": command, "timeout": 10.0})
    assert value["ok"]
    if sys.platform == "win32":
        assert "中文" in value["content"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native command")
def test_native_cmd_chinese_output(tmp_path):
    value = shell.bash(SimpleNamespace(root=tmp_path),
                       {"command": "cmd /c echo 中文路径", "timeout": 10.0})
    assert value["ok"]
    assert "中文路径" in value["content"]


@pytest.mark.parametrize("encoding", ["cp1252", "cp936"])
def test_malformed_utf8_does_not_corrupt_valid_chinese(monkeypatch, encoding):
    monkeypatch.setattr(shell.locale, "getencoding", lambda: encoding)
    if sys.platform == "win32":
        import ctypes
        monkeypatch.setattr(ctypes.windll.kernel32, "GetConsoleOutputCP", lambda: 65001)
    assert shell._decode_output("\ufffd".encode("utf-8")) == "\ufffd"
    data = "中文路径".encode("utf-8")
    assert shell._decode_output(data + b"\xff") == "中文路径\ufffd"
    assert shell._decode_output(data + "中文".encode("utf-8")[:-1]) == "中文路径中\ufffd"
    assert shell._decode_output(data * 10000 + b"\xff") == "中文路径" * 10000 + "\ufffd"


def test_mixed_encoding_lines_decode_independently(monkeypatch):
    monkeypatch.setattr(shell.locale, "getencoding", lambda: "cp936")
    if sys.platform == "win32":
        import ctypes
        monkeypatch.setattr(ctypes.windll.kernel32, "GetConsoleOutputCP", lambda: 65001)
    data = "中文路径\n".encode("utf-8") + "中文路径\n".encode("gbk")
    assert shell._decode_output(data) == "中文路径\n中文路径\n"
