"""audio -> utterances -> text -> Chinese captions.

Two threads. The listener reads audio and recognises it (`listener.py` decides
what is finished and what is still being said). The translator drains a small
job box. Translation is the slow step, so the box keeps only what is still
worth showing: finished speech in order, plus the newest unfinished piece. A
live source captures on a thread of its own and reports how much audio is
waiting, so a slow recognition pass delays captions instead of losing sound.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import numpy as np

from .config import Config
from .glossary import Glossary, Hit
from .listener import Heard, Listener
from .speech import Segmenter, make_recognizer

MAX_PENDING_FINALS = 3
CATCH_UP_SECONDS = 0.25  # more unread audio than this: no partial captions


@dataclass
class Caption:
    utt: int        # utterance number; a later caption with the same utt replaces it
    source: str     # what was heard (after glossary correction)
    text: str       # Chinese
    lang: str
    final: bool
    # Seconds from the moment the speaker stopped (final) or from the newest
    # audio used (partial) to this caption being ready.
    latency: float


@dataclass
class _Job:
    utt: int
    source: str
    lang: str
    hits: list[Hit]
    final: bool
    t_ref: float


class _JobBox:
    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._finals: deque[_Job] = deque()
        self._partial: _Job | None = None
        self._closed = False

    def put(self, job: _Job) -> None:
        with self._cond:
            if job.final:
                self._finals.append(job)
                while len(self._finals) > MAX_PENDING_FINALS:
                    self._finals.popleft()  # too old to matter in a live round
                if self._partial and self._partial.utt <= job.utt:
                    self._partial = None
            else:
                self._partial = job
            self._cond.notify()

    def get(self) -> _Job | None:
        with self._cond:
            while not (self._finals or self._partial or self._closed):
                self._cond.wait()
            if self._finals:
                return self._finals.popleft()
            job, self._partial = self._partial, None
            return job  # None once closed and empty

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()


class Pipeline:
    def __init__(
        self,
        cfg: Config,
        source: Iterable[np.ndarray],
        translator,
        glossary: Glossary,
        on_caption: Callable[[Caption], None],
        recognizer=None,  # one that is already loaded, when the pipeline is rebuilt
    ):
        self.cfg = cfg
        self.source = source
        self.translator = translator
        self.glossary = glossary
        self.on_caption = on_caption
        self.segmenter = Segmenter(cfg.models_path, cfg.vad)
        self.recognizer = recognizer or make_recognizer(cfg.models_path, cfg.asr)
        self._box = _JobBox()
        self._stop = threading.Event()
        self.paused = False  # audio is still read, so nothing piles up, but ignored
        self.recording = None  # a record.Recording while a problem report is being made
        self._final_utt = 0  # highest utterance whose final caption was requested
        self._threads = [
            threading.Thread(target=self._guard, args=(self._listen,), name="listen", daemon=True),
            threading.Thread(target=self._guard, args=(self._translate,), name="translate", daemon=True),
        ]
        self.error: BaseException | None = None
        self.done = threading.Event()  # set when the source is exhausted and drained

    def start(self) -> None:
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        stop_source = getattr(self.source, "stop", None)
        if stop_source:
            stop_source()
        self._box.close()

    def join(self, timeout: float | None = None) -> None:
        for t in self._threads:
            t.join(timeout)
        join_source = getattr(self.source, "join", None)
        if join_source:
            join_source(timeout)

    def _guard(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except BaseException as e:  # surface thread crashes to the main thread
            self.error = e
            self._stop.set()
            self._box.close()
            self.done.set()

    # -- listener ------------------------------------------------------------

    def _heard(self, heard: Heard, now: float) -> None:
        text = self.glossary.correct(heard.text)
        if heard.final:
            self._final_utt = heard.utt
        self._box.put(_Job(heard.utt, text, heard.lang, self.glossary.find(text), heard.final,
                           now - heard.age))

    def _listen(self) -> None:
        listener = Listener(self.segmenter, self.recognizer, self.cfg)
        lag = getattr(self.source, "lag", None)  # seconds of audio waiting to be read
        for block in self.source:
            if self._stop.is_set():
                break
            if self.paused:
                continue
            if self.recording:
                self.recording.audio(block)
            waiting = lag() if lag else 0.0
            now = time.monotonic() - waiting  # when this block was heard
            # A recognition pass that took longer than the audio it covered
            # leaves audio waiting: only finished speech is recognised until
            # the reading is live again.
            for heard in listener.feed(block, live=waiting <= CATCH_UP_SECONDS):
                self._heard(heard, now)
        for heard in listener.flush():
            self._heard(heard, time.monotonic())
        self._box.close()

    # -- translator ------------------------------------------------------------

    def _translate(self) -> None:
        last_key, last_text = None, ""
        # A translator that sends finished and unfinished speech to different
        # models says which one a caption goes to; reuse stays within one.
        path = getattr(self.translator, "path", lambda final: "")
        while True:
            job = self._box.get()
            if job is None:
                break
            key = (job.source, path(job.final))
            if key == last_key:
                # The last partial often already is the whole sentence.
                text = last_text
            else:
                try:
                    text = self.translator.translate(job.source, job.lang, job.hits,
                                                     final=job.final)
                except Exception as e:
                    # Never go silent: fall back to names-only substitution.
                    print(f"translation failed ({e}); showing glossary-only text", file=sys.stderr)
                    text = self.glossary.substitute(job.source)
                if not text:
                    # The model answered with nothing: show what was heard instead.
                    text = self.glossary.substitute(job.source)
                last_key, last_text = key, text
            if not job.final and job.utt <= self._final_utt:
                continue  # the finished version of this utterance is already on its way
            if text:
                caption = Caption(job.utt, job.source, text, job.lang, job.final,
                                  time.monotonic() - job.t_ref)
                if self.recording:
                    self.recording.caption(caption)
                self.on_caption(caption)
        self.done.set()
