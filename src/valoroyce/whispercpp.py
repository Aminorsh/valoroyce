"""Whisper through whisper.cpp built with Vulkan: graphics cards of any maker.

The first choice for every card, NVIDIA included: as accurate and as fast as
faster-whisper on a whole recorded match, without its 1.3 GB of NVIDIA
libraries. whisper.cpp's `whisper-server` runs as a child process, as
llama-server does for translation, and gets each utterance over HTTP on the
loopback address.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import wave
import zlib
from pathlib import Path

import numpy as np

from .config import SAMPLE_RATE, AsrConfig

RUNTIME_DIR = "whisper-cpp"
SERVER = "whisper-server.exe" if os.name == "nt" else "whisper-server"
# Captions use 8177 (llama-server), messages 8178, an own caption model 8179.
HOST, PORT = "127.0.0.1", 8180


def find_server(models: Path) -> Path | None:
    found = sorted((models / RUNTIME_DIR).rglob(SERVER)) if (models / RUNTIME_DIR).exists() else []
    return found[-1] if found else None


def unavailable(models: Path, cfg: AsrConfig) -> str:
    """Why this recogniser cannot be used here, or "" when it can."""
    if not (models / cfg.whispercpp_model).exists():
        return f"its model is not in {models / cfg.whispercpp_model}"
    if find_server(models) is None:
        return f"its runtime (whisper-server) is not in {models / RUNTIME_DIR}"
    from .catalog import VULKAN_MIN_MB, vulkan_card

    if vulkan_card() is None:
        return f"no graphics card with {VULKAN_MIN_MB // 1000} GB of its own memory was found"
    return ""


def compression_ratio(text: str) -> float:
    """How repetitive a text is, the way Whisper's own code measures it."""
    data = text.encode("utf-8")
    return len(data) / len(zlib.compress(data)) if data else 0.0


def _wav(samples: np.ndarray) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return out.getvalue()


def _form(fields: dict[str, str], audio: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
                 f"Content-Type: audio/wav\r\n\r\n".encode() + audio + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class WhisperServer:
    """A whisper-server child process that we own."""

    def __init__(self, models: Path, model: str, threads: int = 4, port: int = PORT, extra: tuple = ()):
        from .translate import _port_free

        server = find_server(models)
        if server is None:
            raise FileNotFoundError(f"{SERVER} not found under {models / RUNTIME_DIR}")
        server = server.resolve()
        self.url = f"http://{HOST}:{port}"
        self._open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
        _port_free(HOST, port, "whisper-server.exe",
                   f"If another program needs port {port}, please report it as an issue: "
                   "this port cannot be changed yet.")
        self.log_path = models / f"whisper-server-{port}.log"
        self._log = open(self.log_path, "wb")
        cmd = [str(server), "-m", str((models / model).resolve()), "--host", HOST, "--port", str(port),
               "-t", str(threads), *extra]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(cmd, stdout=self._log, stderr=subprocess.STDOUT,
                                     creationflags=flags, cwd=str(server.parent))
        handle = getattr(self.proc, "_handle", None)
        if handle is not None:
            from .desktop import end_with_us
            from .fullspeed import full_speed

            end_with_us(int(handle))
            full_speed(int(handle))
        self._wait_ready()

    def _wait_ready(self, timeout: float = 120.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"whisper-server exited with code {self.proc.returncode}; see {self.log_path}")
            try:
                with self._open(self.url + "/health", timeout=2) as resp:
                    if resp.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.2)
        self.close()
        raise RuntimeError(f"whisper-server did not become ready; see {self.log_path}")

    def log(self) -> str:
        self._log.flush()
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    def infer(self, samples: np.ndarray, **fields) -> dict:
        body, kind = _form({k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in fields.items()},
                           _wav(samples))
        req = urllib.request.Request(self.url + "/inference", body, {"Content-Type": kind})
        with self._open(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self._log.close()


class WhisperCppRecognizer:
    """About 0.3 s a pass on a laptop RTX 4060 through Vulkan, like the NVIDIA
    path. The encoder always looks at a full 30-second window: shrinking it
    (`audio_ctx`) left most utterances of a match empty with this model."""

    id, name, label = "whispercpp", "Whisper (Vulkan graphics card)", "Whisper（Vulkan 显卡）"

    def __init__(self, models: Path, cfg: AsrConfig, port: int = PORT):
        self.server = None
        self.languages = cfg.languages or ["ja"]
        problem = unavailable(models, cfg)
        if problem:
            raise RuntimeError(f"Whisper (Vulkan) cannot run: {problem}.")
        self.server = WhisperServer(models, cfg.whispercpp_model, port=port)
        log = self.server.log()
        if "using Vulkan" not in log:
            self.close()
            raise RuntimeError("Whisper (Vulkan) found no graphics card it can use")
        # The card's programs are built on the first pass (14 s on an RTX 4060):
        # done now, while loading, not on the first thing a teammate says.
        self._ask(np.zeros(SAMPLE_RATE, dtype=np.float32), self.languages[0], len(self.languages) > 1)

    def _ask(self, samples: np.ndarray, lang: str, probabilities: bool, prompt: str = "") -> dict:
        fields = dict(response_format="verbose_json", language=lang, temperature="0.0",
                      temperature_inc="0.0", suppress_nst=True, no_language_probabilities=not probabilities)
        if prompt:
            fields["prompt"] = prompt
        return self.server.infer(samples, **fields)

    def transcribe(self, samples: np.ndarray, language: str = "", prompt: str = ""):
        """What was said. `language` given: recognised as that language only;
        `prompt`: text that comes before the audio, which steers the words.
        The server takes one request at a time, so captions and spoken
        messages can share it."""
        from .speech import (_CREDITS, LANGUAGE_PROMPT, MADE_UP_LOGPROB, MADE_UP_REPETITION, OTHER_LANGUAGE,
                             Piece, Transcript, join)

        lang = language or self.languages[0]
        ask_others = not language and len(self.languages) > 1
        reply = self._ask(samples, lang, ask_others, prompt or LANGUAGE_PROMPT.get(language, ""))
        likely = reply.get("language_probabilities") or {}
        if ask_others:
            other = max(self.languages[1:], key=lambda code: likely.get(code, 0.0))
            if likely.get(other, 0.0) >= OTHER_LANGUAGE:
                lang = other
                reply = self._ask(samples, lang, probabilities=False)
        pieces = []
        for s in reply.get("segments", []):
            # Whisper writes text as bytes and may stop inside a character
            # (seen: the first byte of 。 alone); the server sends that as U+FFFD.
            text = (s.get("text") or "").replace("�", "").strip()
            if s.get("avg_logprob", 0.0) < MADE_UP_LOGPROB or compression_ratio(text) > MADE_UP_REPETITION:
                continue
            if text and not _CREDITS.search(text):
                pieces.append(Piece(text, float(s.get("start", 0.0)), float(s.get("end", 0.0))))
        return Transcript(join([p.text for p in pieces], lang), lang, pieces)

    def close(self) -> None:
        if getattr(self, "server", None) is not None:
            self.server.close()
            self.server = None

    __del__ = close  # dropped for another recogniser: the server goes with it
