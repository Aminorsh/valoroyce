"""Running without a console: the port check, one copy at a time, the log."""

import os
import socket
import sys

import pytest

from valoroyce import desktop, translate


def test_a_taken_port_is_reported_with_what_to_do():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        with pytest.raises(RuntimeError, match="Task Manager"):
            translate._port_free("127.0.0.1", port)
    translate._port_free("127.0.0.1", port)  # free again: no error


def test_a_second_start_reaches_the_first(monkeypatch):
    pytest.importorskip("PySide6.QtNetwork")
    # Linux CI machines have no display, and may lack the libraries Qt's widgets load.
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QDeadlineTimer, QEventLoop
    from PySide6.QtWidgets import QApplication

    # The full kind: later tests in this process build windows on the same one.
    app = QApplication.instance() or QApplication([])
    name = f"valoroyce-test-{os.getpid()}"
    assert not desktop.SingleInstance.ask_running("settings", 300, name=name)

    got = []
    first = desktop.SingleInstance(got.append, name=name)
    assert first.listen()
    import subprocess  # the second start is another process, as in real use

    second = subprocess.Popen([sys.executable, "-c", (
        "import sys; from PySide6.QtCore import QCoreApplication; app = QCoreApplication([]); "
        "from valoroyce.desktop import SingleInstance; "
        f"sys.exit(0 if SingleInstance.ask_running('quit', 5000, name={name!r}) else 3)")])
    deadline = QDeadlineTimer(20000)
    while (not got or second.poll() is None) and not deadline.hasExpired():
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
    assert second.wait(5) == 0
    assert got == ["quit"]


def test_output_goes_to_the_log_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    log = tmp_path / "valoroyce.log"
    log.write_text("x" * (desktop._LOG_LIMIT + 1))
    desktop.log_to_file(log)
    print("hello")
    print("note: something", file=sys.stderr)
    sys.stdout.close()
    assert log.read_text(encoding="utf-8") == "hello\nnote: something\n"
    assert (tmp_path / "valoroyce.log.1").stat().st_size > desktop._LOG_LIMIT  # old one kept


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are Windows-only")
def test_a_child_ends_with_us():
    """As llama-server must: the parent ends without stopping its child."""
    import ctypes
    import subprocess

    parent = subprocess.run([sys.executable, "-c", (
        "import os, subprocess, sys; from valoroyce.desktop import end_with_us; "
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "print(c.pid if end_with_us(int(c._handle)) else -1, flush=True); os._exit(0)")],
        capture_output=True, text=True, timeout=30)
    pid = int(parent.stdout.strip())
    assert pid > 0
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
    if handle:  # already gone is fine too
        try:
            assert kernel32.WaitForSingleObject(handle, 5000) == 0  # it ended
        finally:
            kernel32.CloseHandle(handle)
