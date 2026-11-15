"""What has been said so far: finished phrases, and the one still being spoken.

The recognisers are not streaming models, so the utterance in progress is
recognised again every `partial_interval`. Showing each result whole makes a
caption rewrite itself while it is read: the last words of every look are a
guess. So nothing is reported on the strength of one look.

- Speech is finished when the speaker pauses (the voice detector ends the
  utterance), or, inside a long utterance, when the recogniser has written a
  phrase the same way twice in a row and has gone on to a later one. Finished
  speech is reported once, never changes, and its audio is not looked at again.
- Of what follows the finished part, the start that two looks in a row agree
  on is reported as a partial. It only ever grows, until it is finished.

Time is counted in audio received, not on the clock, so the same audio gives
the same captions however fast it is fed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from .config import SAMPLE_RATE, Config
from .speech import Segment, Segmenter, Transcript, join

MIN_PARTIAL_SECONDS = 0.6   # less speech than this is not worth a look yet
MIN_REST_SECONDS = 0.2      # what is left of an utterance after its finished phrases
LONG_SECONDS = 3.0          # with partial captions off, only this much speech is looked at early

_NOT_TEXT = re.compile(r"[\W_]+")


def _letters(text: str) -> str:
    """The text without punctuation, spacing and letter case."""
    return _NOT_TEXT.sub("", text).casefold()


def _same(a: str, b: str) -> bool:
    return bool(_letters(a)) and _letters(a) == _letters(b)


def _steady(now: str, before: str, spaced: bool) -> str:
    """The start of `now` that `before` starts with too, punctuation aside.
    `spaced`: the language separates words, so the result ends on a whole word."""
    want = _letters(before)
    matched = end = 0
    for i, ch in enumerate(now):
        if _NOT_TEXT.fullmatch(ch):
            continue
        if matched == len(want) or ch.casefold() != want[matched]:
            break
        matched, end = matched + 1, i + 1
    else:
        return now
    if spaced and not now[end:end + 1].isspace():
        end = max(now.rfind(" ", 0, end), 0)
    return now[:end].rstrip()


@dataclass
class Heard:
    utt: int      # a later report with the same number replaces this one
    text: str
    lang: str
    final: bool
    age: float    # seconds from the end of this speech to the newest audio received


class Listener:
    def __init__(self, segmenter: Segmenter, recognizer, cfg: Config):
        self.segmenter = segmenter
        self.recognizer = recognizer
        self.interval = cfg.asr.partial_interval
        self.min_silence = cfg.vad.min_silence
        self.partials = cfg.translate.partials  # report speech that is not finished yet
        self.utt = 1
        self._fed = 0                    # samples received so far
        self._looked_at = 0              # value of _fed at the last look at unfinished speech
        self._finished_to = 0            # position in the stream up to which speech is finished
        self._previous: list[str] = []   # the unfinished phrases as the last look had them
        self._reported = ""              # the partial text last reported for this utterance

    def feed(self, block: np.ndarray, live: bool = True) -> list[Heard]:
        """Add audio. `live` is False while the reader is behind the source:
        looks at unfinished speech are optional and are skipped then."""
        self._fed += len(block)
        out: list[Heard] = []
        for seg in self.segmenter.feed(block):
            # The voice detector ends an utterance min_silence after the voice stops.
            out += self._finish(seg, self.min_silence)
        if not live or self.interval <= 0:
            return out
        if self._fed - self._looked_at < self.interval * SAMPLE_RATE:
            return out
        current = self.segmenter.current()
        if current is None:
            return out
        audio, start, spoken = self._rest(current)
        if spoken < (MIN_PARTIAL_SECONDS if self.partials else LONG_SECONDS) * SAMPLE_RATE:
            return out
        self._looked_at = self._fed
        return out + self._look(self.recognizer.transcribe(audio), start, len(audio))

    def flush(self) -> list[Heard]:
        """The source has ended: finish what is still open."""
        out: list[Heard] = []
        for seg in self.segmenter.flush():
            out += self._finish(seg, 0.0)
        return out

    def _rest(self, seg: Segment) -> tuple[np.ndarray, int, int]:
        """The part of an utterance that is not finished yet, where it starts,
        and how much of it is speech rather than the audio before the speech."""
        skip = min(max(0, self._finished_to - seg.start), len(seg.samples))
        audio = seg.samples[skip:]
        return audio, seg.start + skip, len(audio) - max(0, seg.lead - skip)

    def _finish(self, seg: Segment, age: float) -> list[Heard]:
        audio, _, spoken = self._rest(seg)
        self._previous, self._reported = [], ""
        if spoken < MIN_REST_SECONDS * SAMPLE_RATE:
            return []
        heard = self.recognizer.transcribe(audio)
        if not heard.has_content:
            return []
        self.utt += 1
        return [Heard(self.utt - 1, heard.text, heard.lang, True, age)]

    def _look(self, heard: Transcript, start: int, length: int) -> list[Heard]:
        out: list[Heard] = []
        texts = [p.text for p in heard.pieces] if heard.pieces else [heard.text]
        # Phrases before the last one that read as they did a look ago are done.
        agreed = 0
        for now, before in zip(texts[:-1], self._previous):
            if not _same(now, before):
                break
            agreed += 1
        before = join(self._previous[agreed:], heard.lang)
        if agreed:
            end = heard.pieces[agreed - 1].end
            done = Transcript(join(texts[:agreed], heard.lang), heard.lang)
            self._finished_to = start + int(end * SAMPLE_RATE)
            texts = texts[agreed:]
            self._reported = ""
            if done.has_content:
                out.append(Heard(self.utt, done.text, done.lang, True,
                                 max(0.0, length / SAMPLE_RATE - end)))
                self.utt += 1
        self._previous = texts
        if self.partials:
            steady = Transcript(_steady(join(texts, heard.lang), before,
                                        spaced=heard.lang not in ("ja", "zh")), heard.lang)
            if steady.has_content and len(_letters(steady.text)) > len(_letters(self._reported)):
                self._reported = steady.text
                out.append(Heard(self.utt, steady.text, steady.lang, False, 0.0))
        return out
