"""The listener's rules, on scripted speech: no models needed."""
import math

import numpy as np
import pytest

from valoroyce.config import SAMPLE_RATE, Config
from valoroyce.listener import Listener, _steady
from valoroyce.speech import Piece, Segment, Transcript, join

SR = SAMPLE_RATE
GUESS = "ぬ"  # what the fake recogniser adds to a phrase it has not heard to the end


class Script:
    """Speech as a list of ("text", seconds) phrases and (None, seconds) pauses.
    Sample values name the phrase, so the fakes below can tell what they hear."""

    def __init__(self, *items):
        self.texts, parts = {}, []
        for i, (text, seconds) in enumerate(items, start=1):
            n = int(seconds * SR)
            if text is not None:
                self.texts[i] = (text, n)
            parts.append(np.full(n, i if text is not None else 0, dtype=np.float32))
        self.samples = np.concatenate(parts)


class FakeSegmenter:
    """Ends an utterance when `silence` seconds of zeros follow speech."""

    def __init__(self, silence):
        self.silence = int(silence * SR)
        self.stream = np.zeros(0, dtype=np.float32)
        self.start = None   # where the utterance in progress began
        self.done_to = 0

    def _voiced_end(self):
        voiced = np.flatnonzero(self.stream[self.start:])
        return self.start + int(voiced[-1]) + 1

    def feed(self, block):
        self.stream = np.concatenate([self.stream, block])
        out = []
        while True:
            if self.start is None:
                voiced = np.flatnonzero(self.stream[self.done_to:])
                if not len(voiced):
                    return out
                self.start = self.done_to + int(voiced[0])
            tail = self.stream[self.start:]
            quiet = np.flatnonzero(tail == 0)
            # the first run of zeros long enough to be a pause
            end = None
            for q in quiet:
                if q + self.silence <= len(tail) and not tail[q:q + self.silence].any():
                    end = self.start + int(q)
                    break
            if end is None:
                return out
            out.append(Segment(self.stream[self.start:end].copy(), self.start))
            self.done_to, self.start = end, None

    def flush(self):
        if self.start is None:
            return []
        seg = Segment(self.stream[self.start:self._voiced_end()].copy(), self.start)
        self.done_to, self.start = len(self.stream), None
        return [seg]

    def current(self):
        if self.start is None:
            return None
        return Segment(self.stream[self.start:].copy(), self.start)


class FakeRecognizer:
    """Writes each phrase in proportion to how much of it is in the audio, and
    ends a phrase it has not heard to the end with a guess, as Whisper does."""

    def __init__(self, script, pieces=True, lang="ja"):
        self.script, self.pieces, self.lang = script, pieces, lang
        self.heard = []  # the audio of every pass

    def transcribe(self, samples):
        self.heard.append(samples.copy())
        pieces, i = [], 0
        while i < len(samples):
            j = i
            while j < len(samples) and samples[j] == samples[i]:
                j += 1
            name = int(samples[i])
            if name:
                text, full = self.script.texts[name]
                if j - i < full:
                    text = text[:math.ceil(len(text) * (j - i) / full) - 1] + GUESS
                pieces.append(Piece(text, i / SR, j / SR))
            i = j
        text = join([p.text for p in pieces], self.lang)
        return Transcript(text, self.lang, pieces if self.pieces else [])


def listen(script, block=0.1, live=True, pieces=True, lang="ja", **settings):
    cfg = Config()
    cfg.asr.partial_interval = settings.pop("interval", 0.5)
    cfg.translate.partials = settings.pop("partials", True)
    assert not settings
    recognizer = FakeRecognizer(script, pieces, lang)
    listener = Listener(FakeSegmenter(cfg.vad.min_silence), recognizer, cfg)
    reports, n = [], int(block * SR)
    for i in range(0, len(script.samples), n):
        for h in listener.feed(script.samples[i:i + n], live=live):
            reports.append(((i + n) / SR, h))
    reports += [(len(script.samples) / SR, h) for h in listener.flush()]
    return reports, recognizer


def finals(reports):
    return [h.text for _, h in reports if h.final]


def test_a_pause_finishes_what_was_said():
    reports, _ = listen(Script(("ジェットがAにいる", 1.5), (None, 1.0)))
    assert finals(reports) == ["ジェットがAにいる"]
    when, last = reports[-1]
    assert last.final and last.age == Config().vad.min_silence
    assert when == pytest.approx(1.5 + Config().vad.min_silence, abs=0.11)


def test_partials_only_grow_and_never_show_a_guess():
    said = "スモーク出すからみんなでBに入ろう"
    reports, _ = listen(Script((said, 4.0), (None, 1.0)))
    partials = [h.text for _, h in reports if not h.final]
    assert len(partials) >= 3
    assert all(GUESS not in p and said.startswith(p) for p in partials)
    assert all(len(a) < len(b) for a, b in zip(partials, partials[1:]))
    # One number for the whole utterance: the final replaces its partials.
    assert {h.utt for _, h in reports} == {1} and reports[-1][1].final


def test_one_look_alone_reports_nothing():
    # Shorter than two looks: the caption appears once, finished.
    reports, recognizer = listen(Script(("ナイス", 0.6), (None, 1.0)))
    assert [h.final for _, h in reports] == [True]
    assert len(recognizer.heard) == 2  # one look, then the pass that finished it


def test_phrases_of_a_long_utterance_are_finished_early_and_once():
    script = Script(("敵がミッドから来てる", 2.0), ("スモーク出すよ", 2.0), ("Aに行こう", 2.0), (None, 1.0))
    reports, recognizer = listen(script)
    assert finals(reports) == ["敵がミッドから来てる", "スモーク出すよ", "Aに行こう"]
    done = [(when, h) for when, h in reports if h.final]
    # The first two were finished while the speaker was still talking.
    assert done[0][0] < 4.0 and done[1][0] < 6.0 and done[0][1].age > 0
    assert [h.utt for _, h in done] == [1, 2, 3]
    # A later report never touches a finished number.
    for when, h in reports:
        assert all(h.utt > d.utt for at, d in done if at < when)
    # Finished audio is not recognised again: the last pass hears the third phrase only.
    assert set(np.unique(recognizer.heard[-1])) == {3.0}


def test_finished_speech_is_cut_where_the_recogniser_said_it_ends():
    script = Script(("あいうえお", 2.0), ("かきくけこ", 3.0), (None, 1.0))
    reports, recognizer = listen(script)
    early = next(h for _, h in reports if h.final)
    assert early.text == "あいうえお"
    after = recognizer.heard[len(recognizer.heard) - 1]
    assert len(after) == 3 * SR  # exactly the second phrase


def test_a_recogniser_without_phrase_times_waits_for_the_pause():
    script = Script(("敵がミッドから来てる", 2.0), ("スモーク出すよ", 2.0), (None, 1.0))
    reports, _ = listen(script, pieces=False)
    assert finals(reports) == ["敵がミッドから来てるスモーク出すよ"]
    assert all(GUESS not in h.text for _, h in reports)


def test_no_looks_while_the_reader_is_behind():
    script = Script(("敵がミッドから来てる", 2.0), ("スモーク出すよ", 2.0), (None, 1.0), ("ナイス", 1.0), (None, 1.0))
    reports, recognizer = listen(script, live=False)
    assert all(h.final for _, h in reports)
    assert len(recognizer.heard) == 2  # one pass per utterance
    assert finals(reports) == ["敵がミッドから来てるスモーク出すよ", "ナイス"]


def test_interval_zero_means_captions_at_pauses_only():
    reports, recognizer = listen(Script(("スモーク出すよ", 3.0), (None, 1.0)), interval=0.0)
    assert [h.final for _, h in reports] == [True] and len(recognizer.heard) == 1


def test_without_partial_captions_only_long_speech_is_looked_at_early():
    short = Script(("スモーク出すよ", 2.5), (None, 1.0))
    reports, recognizer = listen(short, partials=False)
    assert [h.final for _, h in reports] == [True] and len(recognizer.heard) == 1
    long = Script(("敵がミッドから来てる", 2.0), ("スモーク出すよ", 2.0), ("Aに行こう", 2.0), (None, 1.0))
    reports, _ = listen(long, partials=False)
    assert all(h.final for _, h in reports)
    assert finals(reports) == ["敵がミッドから来てる", "スモーク出すよ", "Aに行こう"]


def test_the_same_audio_gives_the_same_captions_however_it_is_cut_into_blocks():
    script = Script(("敵がミッドから来てる", 2.0), ("スモーク出すよ", 2.0), (None, 0.6), ("ナイス", 1.0), (None, 1.0))
    a, _ = listen(script, block=0.1)
    b, _ = listen(script, block=0.02)
    assert finals(a) == finals(b) == ["敵がミッドから来てる", "スモーク出すよ", "ナイス"]


def test_the_end_of_the_source_finishes_open_speech():
    reports, _ = listen(Script(("スモーク出すよ", 1.0)))
    assert finals(reports) == ["スモーク出すよ"] and reports[-1][1].age == 0.0


def test_english_partials_end_on_a_whole_word():
    reports, _ = listen(Script(("they are pushing through mid right now", 4.0), (None, 1.0)), lang="en")
    partials = [h.text for _, h in reports if not h.final]
    assert partials and all(p in ("they", "they are", "they are pushing", "they are pushing through",
                                  "they are pushing through mid", "they are pushing through mid right")
                            for p in partials)


def test_steady_ignores_punctuation_and_stops_at_the_first_difference():
    assert _steady("えー、ネオンくんいいさあ", "えーネオンくんいい", spaced=False) == "えー、ネオンくんいい"
    assert _steady("ラマすんなよな", "騙すんなよなぁ", spaced=False) == ""
    assert _steady("Jett is low", "jett is lo", spaced=True) == "Jett is"
    assert _steady("Jett is low", "Jett is low.", spaced=True) == "Jett is low"
    assert _steady("anything", "", spaced=True) == ""

def test_the_audio_before_the_speech_does_not_count_as_speech():
    # A blip too short to be worth a pass stays so with its lead in front.
    class OneBlip:
        def feed(self, block):
            return [Segment(np.ones(int(0.5 * SR), dtype=np.float32), 0, lead=int(0.4 * SR))]

        def flush(self):
            return []

        def current(self):
            return None

    script = Script(("あ", 1.0))
    recognizer = FakeRecognizer(script)
    listener = Listener(OneBlip(), recognizer, Config())
    assert listener.feed(np.zeros(1600, dtype=np.float32)) == [] and not recognizer.heard
