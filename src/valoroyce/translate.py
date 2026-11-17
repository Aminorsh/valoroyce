"""Translation backends.

The default runs Tencent's Hy-MT2 (a 1.8B translation model, Apache-2.0) on a
local llama.cpp server that we start ourselves. Nothing leaves the machine.
Any OpenAI-compatible server works too (Ollama, LM Studio, a remote API).
"""

from __future__ import annotations

import dataclasses
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from .config import TranslateConfig
from .glossary import Glossary, Hit

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
# Languages a player can write to, as the translation prompt names them.
OUTGOING = {"ja": "日语", "en": "英语"}
# What a model is told to answer when it will not translate a sentence. Hosted
# models otherwise put their refusal where the translation belongs, and it
# would be pasted to teammates.
DECLINED = "###"


class Declined(RuntimeError):
    """The model answered, but not with a translation."""


class Unreachable(RuntimeError):
    """Nothing answers at that address: no server running there, or no such host.
    Unlike a timeout, trying again at once will not help."""


def _opener(base_url: str) -> urllib.request.OpenerDirector:
    # A system proxy must never sit between us and a server on this machine.
    if urlparse(base_url).hostname in _LOOPBACK:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


class GlossaryTranslator:
    """No model: swap known terms for their Chinese and show the rest as heard."""

    def __init__(self, glossary: Glossary):
        self.glossary = glossary

    def translate(self, text: str, lang: str, hits: list[Hit], final: bool = True) -> str:
        return self.glossary.substitute(text)

    def compose(self, text: str, lang: str) -> str:
        text, pairs = self.glossary.outgoing(text, lang)
        for zh, name in pairs:
            text = text.replace(zh, name)
        return text

    def close(self) -> None:
        pass


def build_messages(
    text: str,
    pairs: list[tuple[str, str]],
    cfg: TranslateConfig,
    target: str = "",
    may_decline: bool = False,
) -> list[dict[str, str]]:
    """Prompt for one line. `pairs` are (term as written, term in the target
    language). `target` is set when translating the player's own Chinese
    into another language; empty means captions, into cfg.target.
    `may_decline` asks the model to answer DECLINED instead of refusing in prose."""
    outgoing, target = bool(target), target or cfg.target
    if cfg.prompt == "hy-mt":
        # Hy-MT's documented formats: plain, and "terminology intervention".
        prompt = ""
        if pairs:
            prompt += "参考下面的翻译：\n"
            prompt += "\n".join(f"{src} 翻译成 {zh}" for src, zh in pairs) + "\n\n"
        prompt += f"将以下文本翻译为{target}，注意只需要输出翻译后的结果，不要额外解释：\n{text}"
        return [{"role": "user", "content": prompt}]
    if cfg.prompt == "generic":
        if outgoing:
            system = (
                "你在帮一位《无畏契约》(VALORANT) 玩家给队友发文字消息。"
                f"把他写的中文翻译成简短、自然的{target}，用玩家之间打字聊天的口吻。"
                "只输出译文，不要解释，不要加引号。"
            )
            if may_decline:
                system += f"\n如果这句话你不能或不愿翻译，只输出三个字符：{DECLINED}"
        else:
            system = (
                "你是《无畏契约》(VALORANT) 队内语音的同声传译。"
                f"把玩家说的话翻译成简短、口语化的{target}，使用国服玩家的说法。"
                "只输出译文，不要解释，不要加引号。"
            )
            if may_decline:
                system += f"\n如果这句话你不能或不愿翻译，只输出三个字符：{DECLINED}"
        if pairs:
            system += "\n术语表：\n" + "\n".join(f"{src} = {zh}" for src, zh in pairs)
        return [{"role": "system", "content": system}, {"role": "user", "content": text}]
    raise ValueError(f"unknown translate.prompt: {cfg.prompt!r}")


def clean_output(text: str) -> str:
    text = text.strip()
    if "\n" in text:
        text = text.split("\n", 1)[0].strip()
    if len(text) >= 2 and text[0] in "\"“「『" and text[-1] in "\"”」』":
        text = text[1:-1].strip()
    return text


class ChatTranslator:
    """Any server that speaks OpenAI's /v1/chat/completions."""

    def __init__(self, cfg: TranslateConfig, glossary: Glossary):
        self.cfg = cfg
        self.glossary = glossary
        self.url = cfg.base_url.rstrip("/") + "/v1/chat/completions"
        self._open = _opener(cfg.base_url).open
        # Set when another translator can take over a sentence this one declines.
        self.may_decline = False

    def translate(self, text: str, lang: str, hits: list[Hit], final: bool = True) -> str:
        """A line that was heard -> Chinese caption. `final` is False for a
        sentence that is still being spoken."""
        pairs = [(h.source, h.zh) for h in hits]
        out = self._chat(build_messages(text, pairs, self.cfg, may_decline=self.may_decline))
        if self.may_decline and (not out or out.startswith(DECLINED)):
            raise Declined("the model did not translate this sentence")
        return self.glossary.enforce(out, hits)

    def compose(self, text: str, lang: str) -> str:
        """Chinese the player typed -> a message in `lang` ("ja" or "en")."""
        text, pairs = self.glossary.outgoing(text, lang)
        out = self._chat(build_messages(text, pairs, self.cfg, target=OUTGOING[lang],
                                        may_decline=self.may_decline))
        if not out or (self.may_decline and out.startswith(DECLINED)):
            raise Declined("the model did not translate this sentence")
        return out

    def _chat(self, messages: list[dict[str, str]]) -> str:
        body = {
            "model": self.cfg.model,
            "messages": messages,
            "max_tokens": self.cfg.max_tokens,
            # Low temperature: a caption that is re-translated as the speaker
            # continues should not reword itself for no reason.
            "temperature": 0.2,
            "top_p": 0.6,
            "stream": False,
        }
        if self.cfg.prompt == "hy-mt":
            # llama.cpp extensions, from the model card's recommended settings.
            body.update({"top_k": 20, "repeat_penalty": 1.05, "cache_prompt": True})
        body.update(self.cfg.extra_body)
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        data = json.dumps(body).encode()
        host = urlparse(self.url).hostname
        try:
            try:
                with self._open(urllib.request.Request(self.url, data, headers),
                                timeout=self.cfg.timeout) as resp:
                    reply = json.load(resp)
            except urllib.error.HTTPError as e:
                # "http://" for a service that wants "https://": urllib does not
                # repeat a POST at the new address by itself. Follow it, and keep it.
                moved = e.headers.get("Location", "") if e.code in (307, 308) else ""
                if not moved.startswith(("http://", "https://")):
                    raise
                self.url = moved
                with self._open(urllib.request.Request(self.url, data, headers),
                                timeout=self.cfg.timeout) as resp:
                    reply = json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RuntimeError(f"{host} rejected the API key (HTTP {e.code}). "
                                   "Check api_key in config.toml.") from None
            if e.code == 402:
                raise RuntimeError(f"{host} says the account has no balance left "
                                   "(HTTP 402).") from None
            detail = e.read(300).decode("utf-8", "replace").strip()
            raise RuntimeError(f"{host} answered HTTP {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            reason = getattr(e, "reason", e)
            kind = (Unreachable if isinstance(reason, (ConnectionRefusedError, socket.gaierror))
                    else RuntimeError)
            raise kind(f"cannot reach {host} ({reason}). Is the network up, "
                       "and is base_url right?") from None
        return clean_output(reply["choices"][0]["message"]["content"] or "")

    def close(self) -> None:
        pass


def find_llama_server(models: Path) -> Path:
    name = "llama-server.exe" if os.name == "nt" else "llama-server"
    found = sorted((models / "llama").rglob(name)) if (models / "llama").exists() else []
    if not found:
        raise FileNotFoundError(
            f"{name} not found under {models / 'llama'}. Run `valoroyce setup`, "
            "or set translate.backend = \"openai\" to use a server you run yourself."
        )
    return found[-1]


CAPTION_MODEL = "Hy-MT2-1.8B-Q4_K_M.gguf"


def find_gguf(models: Path, name: str = "") -> Path:
    """The model file to serve: `name` if given, else the bundled caption model,
    else whatever single .gguf the user put there."""
    if name:
        if not (models / name).exists():
            raise FileNotFoundError(f"{models / name} is missing. Run `valoroyce setup`.")
        return models / name
    if (models / CAPTION_MODEL).exists():
        return models / CAPTION_MODEL
    found = sorted(models.glob("*.gguf"))
    if not found:
        raise FileNotFoundError(f"no .gguf model in {models}. Run `valoroyce setup`.")
    return found[0]


def _port_free(host: str, port: int) -> None:
    """Fail with what to do when something already listens where our server
    should. Otherwise our llama-server cannot start, or we would talk to a
    stranger."""
    import socket

    with socket.socket() as s:
        s.settimeout(0.5)
        if s.connect_ex((host, port)) != 0:
            return
    raise RuntimeError(
        f"port {port} is already in use, probably by a llama-server.exe left running by an "
        f"earlier start. End llama-server.exe in Task Manager (or restart the PC), then start "
        f"again. If another program needs that port, change [translate] base_url in config.toml."
    )


class LlamaTranslator(ChatTranslator):
    """ChatTranslator plus a llama-server child process that we own."""

    def __init__(self, cfg: TranslateConfig, glossary: Glossary, models: Path):
        super().__init__(cfg, glossary)
        # Absolute: the child runs with its own directory as cwd (for its DLLs).
        server = find_llama_server(models).resolve()
        model = find_gguf(models, cfg.model_file).resolve()
        parsed = urlparse(cfg.base_url)
        _port_free(parsed.hostname or "127.0.0.1", parsed.port or 8177)
        cmd = [
            str(server), "-m", str(model),
            "--host", parsed.hostname or "127.0.0.1", "--port", str(parsed.port or 8177),
            "-c", "2048", "-t", str(cfg.threads),
            "-ngl", str(cfg.gpu_layers if cfg.gpu else 0),
            # A translator must answer at once: no "thinking" phase, on models that have one.
            "--jinja", "--reasoning", "off", *cfg.server_args,
        ]
        self.log_path = models / f"llama-server-{parsed.port or 8177}.log"
        self._log = open(self.log_path, "wb")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            cmd, stdout=self._log, stderr=subprocess.STDOUT, creationflags=flags,
            cwd=str(server.parent),
        )
        handle = getattr(self.proc, "_handle", None)  # Windows: the handle Popen holds for its child
        if handle is not None:
            from .desktop import end_with_us
            from .fullspeed import full_speed

            end_with_us(int(handle))  # never left running after we end
            full_speed(int(handle))
        self._wait_ready()

    def _wait_ready(self, timeout: float = 120.0) -> None:
        health = self.cfg.base_url.rstrip("/") + "/health"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"llama-server exited with code {self.proc.returncode}; see {self.log_path}"
                )
            try:
                with self._open(health, timeout=2) as resp:
                    if resp.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.3)
        self.close()
        raise RuntimeError(f"llama-server did not become ready; see {self.log_path}")

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self._log.close()


class ComposeWithFallback:
    """Outgoing messages on a model of their own. When it cannot be reached,
    or declines a sentence, the caption translator does that sentence. So it
    does for a sentence containing a `local_only` word, which is never sent."""

    def __init__(self, own, fallback, local_only: list[str] = ()):
        self.own = own
        self.fallback = fallback  # () -> the caption translator, or None if there is none
        self.local_only = [w.lower() for w in local_only if w]
        # Why the last message was done by the caption translator; "" if it was not.
        self.fell_back = ""

    def compose(self, text: str, lang: str) -> str:
        self.fell_back = ""
        if any(w in text.lower() for w in self.local_only):
            other = self.fallback()
            if other is None:  # never the other model, not even as a last resort
                raise RuntimeError("the local model is not ready yet; try again in a moment")
            self.fell_back = "kept on this computer by local_only"
            return other.compose(text, lang)
        try:
            return self.own.compose(text, lang)
        except Exception as e:
            other = self.fallback()
            if other is None:
                raise
            out = other.compose(text, lang)
            self.fell_back = str(e)
            print(f"note: message translated by the caption model ({e})", file=sys.stderr, flush=True)
            return out

    def close(self) -> None:
        self.own.close()


class CaptionWithFallback:
    """Captions on the player's own model, with the usual caption translator
    behind it. A sentence the own model fails on, declines, or does not answer
    within its timeout is done by the fallback. After FAILURES_TO_REST failures
    in a row the own model rests for REST seconds, so a lost network or a
    stopped server does not hold up every caption by a timeout."""

    FAILURES_TO_REST = 2
    REST = 30.0

    def __init__(self, own, fallback, partials: bool = False, notify=None,
                 clock=time.monotonic):
        self.own, self.fallback = own, fallback
        self.partials = partials  # half-finished captions go to the own model too
        # Told when the own model starts resting; once, until it works again.
        self.notify = notify or (lambda why: None)
        self._told = False
        self.clock = clock
        self._failures = 0
        self._rest_until = 0.0
        # Why the last caption was done by the fallback; "" if it was not.
        self.fell_back = ""

    def path(self, final: bool) -> str:
        """Which model a caption goes to: the pipeline reuses a translation only
        within one path, so a finished sentence is not left with the fallback's
        version of its last partial."""
        return "own" if final or self.partials else "fallback"

    def translate(self, text: str, lang: str, hits: list[Hit], final: bool = True) -> str:
        self.fell_back = ""
        if self.path(final) == "fallback":
            return self.fallback.translate(text, lang, hits, final=final)
        if self.clock() < self._rest_until:
            self.fell_back = "resting after failures"
            return self.fallback.translate(text, lang, hits, final=final)
        try:
            out = self.own.translate(text, lang, hits, final=final)
            self._failures, self._told = 0, False
            if out:
                return out
            self.fell_back = "empty reply"
        except Declined as e:
            self._failures, self._told = 0, False  # it works; it only would not do this one
            self.fell_back = str(e)
        except Exception as e:
            self.fell_back = str(e)
            # A timeout may be a passing thing; a refused connection is not
            # (and on Windows it takes 2 s to be refused).
            self._failures += self.FAILURES_TO_REST if isinstance(e, Unreachable) else 1
            if self._failures >= self.FAILURES_TO_REST:
                self._failures = 0
                self._rest_until = self.clock() + self.REST
                print(f"note: own caption model rests for {self.REST:.0f} s ({e})",
                      file=sys.stderr, flush=True)
                if not self._told:
                    self._told = True
                    self.notify(str(e))
        return self.fallback.translate(text, lang, hits, final=final)

    def compose(self, text: str, lang: str) -> str:
        # Messages are not the caption model's business here: they stay on the
        # local model (or the [compose] model, which wraps this one).
        return self.fallback.compose(text, lang)

    def close(self) -> None:
        try:
            self.own.close()
        finally:
            self.fallback.close()


def make_compose_translator(cfg, glossary: Glossary, models: Path, fallback=None):
    """A translator of its own for outgoing messages, or None when the config
    does not ask for one (then the caption translator is used). `fallback`
    returns the caption translator, for sentences the other one fails on."""
    own = _compose_backend(cfg, glossary, models)
    if not (own and fallback):
        return own
    # Hosted models refuse some sentences; a model we run ourselves does not.
    own.may_decline = cfg.compose.backend == "openai"
    return ComposeWithFallback(own, fallback, cfg.compose.local_only)


def _compose_backend(cfg, glossary: Glossary, models: Path):
    c = cfg.compose
    if not c.backend:
        return None
    own = dataclasses.replace(
        cfg.translate, backend=c.backend, model_file=c.model_file, base_url=c.base_url,
        api_key=c.api_key or os.environ.get("VALOROYCE_COMPOSE_API_KEY", ""),
        model=c.model or cfg.translate.model, prompt=c.prompt, timeout=c.timeout,
        extra_body=c.extra_body,
    )
    if c.backend == "openai":
        return ChatTranslator(own, glossary)
    if c.backend == "llama":
        if not c.model_file:
            raise ValueError('[compose] backend = "llama" needs model_file: the name of '
                             "a .gguf file in the models folder")
        if cfg.translate.backend == "llama" and c.base_url == cfg.translate.base_url:
            raise ValueError("[compose] base_url must use a different port than "
                             "[translate] base_url")
        return LlamaTranslator(own, glossary, models)
    raise ValueError(f"unknown compose.backend: {c.backend!r}")


def make_translator(cfg: TranslateConfig, glossary: Glossary, models: Path):
    if cfg.backend == "none":
        return GlossaryTranslator(glossary)
    if cfg.backend == "openai":
        return ChatTranslator(cfg, glossary)
    if cfg.backend == "llama":
        print("starting local translation model...", file=sys.stderr, flush=True)
        return LlamaTranslator(cfg, glossary, models)
    raise ValueError(f"unknown translate.backend: {cfg.backend!r}")


def is_online(base_url: str) -> bool:
    """Whether a server address leaves this computer."""
    return urlparse(base_url).hostname not in _LOOPBACK


def _custom_backend(cfg: TranslateConfig, glossary: Glossary, models: Path):
    c = cfg.custom
    own = dataclasses.replace(
        cfg, backend=c.backend, base_url=c.base_url,
        api_key=c.api_key or os.environ.get("VALOROYCE_CAPTION_API_KEY", ""),
        model=c.model or cfg.model, prompt=c.prompt, timeout=c.timeout,
        extra_body=c.extra_body, model_file=c.model_file,
    )
    if c.backend == "openai":
        return ChatTranslator(own, glossary)
    if c.backend == "llama":
        if not c.model_file:
            raise ValueError('[translate.custom] backend = "llama" needs model_file: the name '
                             "of a .gguf file in the models folder")
        if is_online(c.base_url):
            raise ValueError("[translate.custom] base_url must be on this computer "
                             '(127.0.0.1) for backend = "llama"')
        if cfg.backend == "llama" and urlparse(c.base_url).port == urlparse(cfg.base_url).port:
            raise ValueError("[translate.custom] base_url must use a different port than "
                             "[translate] base_url")
        print("starting your own caption model...", file=sys.stderr, flush=True)
        return LlamaTranslator(own, glossary, models)
    raise ValueError(f"unknown translate.custom.backend: {c.backend!r}")


def make_caption_translator(cfg: TranslateConfig, glossary: Glossary, models: Path,
                            notify=None):
    """The caption translator: the model of [translate], with the player's own
    model in front of it when [translate.custom] names one. An own model that
    cannot start is reported through `notify(why)` and left out: captions go
    on with the model behind it."""
    main = make_translator(cfg, glossary, models)
    if not cfg.custom.backend:
        return main
    try:
        own = _custom_backend(cfg, glossary, models)
    except Exception as e:
        print(f"warning: own caption model not available ({e})", file=sys.stderr, flush=True)
        if notify:
            notify(str(e))
        return main
    own.may_decline = True  # something stands behind it
    return CaptionWithFallback(own, main, cfg.custom.partials, notify)
