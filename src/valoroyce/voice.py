"""Speak a message instead of typing it: one sentence from the microphone.

Listening ends when the speaker pauses, when `stop()` is called (the shortcut
pressed again), or after `MAX_SECONDS`. The sentence is recognised as Chinese
by X-ASR when it is downloaded, else by the captions' recogniser (the caller
chooses). Nothing here touches the game: the
microphone is read the way any recording program reads it, beside the game.
"""

from __future__ import annotations

import dataclasses
import re
import sys
import threading
import time

import numpy as np

from .config import Config

LANGUAGE = "zh"
# A spoken message may have short pauses while thinking; captions use less.
PAUSE_SECONDS = 0.8
MAX_SECONDS = 15.0
# Nobody started talking within this time: give up and say so.
WAIT_SECONDS = 6.0


# Read by Whisper before the audio: simplified characters, game context.
# Listing the game's names here did not help: they sound like ordinary words
# (暮蝶 came out as 目蝶 with or without them), and Whisper keeps only the end
# of a long prompt. Names are put right afterwards, by sound (Glossary.spoken_zh).
PROMPT = "以下是普通话的句子，是在玩无畏契约时说的。"
_FULL_WIDTH = str.maketrans({",": "，", "?": "？", "!": "！", ";": "；", ":": "："})


def tidy(text: str, glossary=None) -> str:
    """Chinese punctuation (Whisper writes ASCII commas), a lone letter as a
    capital beside the Chinese ("a 包" -> "A包"), names by sound."""
    text = re.sub(r"(?<![0-9A-Za-z])[,?!;:]|[,?!;:](?![0-9A-Za-z])",
                  lambda m: m.group(0).translate(_FULL_WIDTH), text)
    text = re.sub(r"\s*(?<![A-Za-z])([A-Za-z])(?![A-Za-z])\s*", lambda m: m.group(1).upper(), text)
    return glossary.spoken_zh(text) if glossary is not None else text


class Listening:
    """One sentence, recognised on a thread of its own.

    `on_state(text)` reports progress for the window, `on_done(text, error)`
    the result: text is "" when nothing was heard or something failed.
    """

    def __init__(self, cfg: Config, recognizer, on_state, on_done, source=None, segmenter=None,
                 glossary=None):
        self.cfg, self.recognizer, self.glossary = cfg, recognizer, glossary
        self.on_state, self.on_done = on_state, on_done
        self._source, self._segmenter = source, segmenter
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="voice", daemon=True)

    def start(self) -> "Listening":
        self._thread.start()
        return self

    def stop(self) -> None:
        """Finish now: what was said so far is recognised."""
        self._stop.set()

    @property
    def active(self) -> bool:
        return self._thread.is_alive()

    def _open(self):
        if self._source is not None:
            return self._source, self._segmenter
        from .audio import DeviceSource
        from .speech import Segmenter

        vad = dataclasses.replace(self.cfg.vad, min_silence=PAUSE_SECONDS, max_speech=MAX_SECONDS)
        try:
            source = DeviceSource(self.cfg.compose.mic, loopback=False)
        except Exception as e:
            raise RuntimeError(f"麦克风无法使用：{e}。请在「设置与模型」的「发消息」页选择麦克风。") from e
        return source, Segmenter(self.cfg.models_path, vad)

    def _run(self) -> None:
        try:
            if sys.platform == "win32" and self._source is None:
                # A thread that opens an audio device must be in COM: without
                # it the second message failed (0x800401F0). soundcard joins
                # COM itself, once, in the thread that imports it, and fails
                # if that thread is in already: so it is imported first.
                from .apploop import com_init
                from .audio import _soundcard

                _soundcard()
                com_init()
            audio = self._listen()
            if audio is None:
                self.on_done("", "没有听到说话。请靠近麦克风再试一次。")
                return
            self.on_state("识别中…")
            text = self.recognizer.transcribe(audio, language=LANGUAGE, prompt=PROMPT).text.strip()
            text = tidy(text, self.glossary)
            self.on_done(text, "" if text else "没有听清，请再说一次。")
        except Exception as e:
            self.on_done("", str(e))

    def _listen(self) -> np.ndarray | None:
        """The sentence's audio, or None when nobody spoke."""
        source, segmenter = self._open()
        heard: list[np.ndarray] = []
        started = time.monotonic()
        speaking = False
        self.on_state("● 正在听…  说完停一下就好")
        try:
            for block in source:
                done = segmenter.feed(block)
                if done:  # the speaker paused: the sentence is complete
                    heard = [s.samples for s in done]
                    break
                current = segmenter.current()
                if current is not None and not speaking:
                    speaking = True
                    self.on_state("● 正在听…  说完停一下就好（再按一次快捷键立即结束）")
                waited = time.monotonic() - started
                if self._stop.is_set() or waited > MAX_SECONDS + 1:
                    # Ending the detector hands out the sentence in progress.
                    heard = [s.samples for s in segmenter.flush()]
                    if not heard and current is not None:
                        heard = [current.samples]
                    break
                if not speaking and waited > WAIT_SECONDS:
                    break
        finally:
            stop = getattr(source, "stop", None)
            if stop:
                stop()
                source.join(2.0)
        heard = [h for h in heard if len(h)]
        if not heard:
            return None
        return np.concatenate(heard).astype(np.float32)


def listen(cfg: Config, recognizer, on_state, on_done, glossary=None) -> Listening:
    return Listening(cfg, recognizer, on_state, on_done, glossary=glossary).start()
