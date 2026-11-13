"""Translation backends.

The default runs Tencent's Hy-MT2 (a 1.8B translation model, Apache-2.0) on a
local llama.cpp server that we start ourselves. Nothing leaves the machine.
Any OpenAI-compatible server works too (Ollama, LM Studio, a remote API).
"""

from __future__ import annotations

import json
import os
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


def _opener(base_url: str) -> urllib.request.OpenerDirector:
    # A system proxy must never sit between us and a server on this machine.
    if urlparse(base_url).hostname in _LOOPBACK:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


class GlossaryTranslator:
    """No model: swap known terms for their Chinese and show the rest as heard."""

    def __init__(self, glossary: Glossary):
        self.glossary = glossary

    def translate(self, text: str, lang: str, hits: list[Hit]) -> str:
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
) -> list[dict[str, str]]:
    """Prompt for one line. `pairs` are (term as written, term in the target
    language). `target` is set when translating the player's own Chinese
    into another language; empty means captions, into cfg.target."""
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
        else:
            system = (
                "你是《无畏契约》(VALORANT) 队内语音的同声传译。"
                f"把玩家说的话翻译成简短、口语化的{target}，使用国服玩家的说法。"
                "只输出译文，不要解释，不要加引号。"
            )
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

    def translate(self, text: str, lang: str, hits: list[Hit]) -> str:
        """A line that was heard -> Chinese caption."""
        pairs = [(h.source, h.zh) for h in hits]
        out = self._chat(build_messages(text, pairs, self.cfg))
        return self.glossary.enforce(out, hits)

    def compose(self, text: str, lang: str) -> str:
        """Chinese the player typed -> a message in `lang` ("ja" or "en")."""
        text, pairs = self.glossary.outgoing(text, lang)
        return self._chat(build_messages(text, pairs, self.cfg, target=OUTGOING[lang]))

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
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        req = urllib.request.Request(self.url, json.dumps(body).encode(), headers)
        with self._open(req, timeout=self.cfg.timeout) as resp:
            reply = json.load(resp)
        return clean_output(reply["choices"][0]["message"]["content"])

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


def find_gguf(models: Path) -> Path:
    found = sorted(models.glob("*.gguf"))
    if not found:
        raise FileNotFoundError(f"no .gguf model in {models}. Run `valoroyce setup`.")
    return found[0]


class LlamaTranslator(ChatTranslator):
    """ChatTranslator plus a llama-server child process that we own."""

    def __init__(self, cfg: TranslateConfig, glossary: Glossary, models: Path):
        super().__init__(cfg, glossary)
        # Absolute: the child runs with its own directory as cwd (for its DLLs).
        server = find_llama_server(models).resolve()
        model = find_gguf(models).resolve()
        parsed = urlparse(cfg.base_url)
        cmd = [
            str(server), "-m", str(model),
            "--host", parsed.hostname or "127.0.0.1", "--port", str(parsed.port or 8177),
            "-c", "2048", "-t", str(cfg.threads),
            "-ngl", str(cfg.gpu_layers if cfg.gpu else 0),
            "--jinja", *cfg.server_args,
        ]
        self.log_path = models / "llama-server.log"
        self._log = open(self.log_path, "wb")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            cmd, stdout=self._log, stderr=subprocess.STDOUT, creationflags=flags,
            cwd=str(server.parent),
        )
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


def make_translator(cfg: TranslateConfig, glossary: Glossary, models: Path):
    if cfg.backend == "none":
        return GlossaryTranslator(glossary)
    if cfg.backend == "openai":
        return ChatTranslator(cfg, glossary)
    if cfg.backend == "llama":
        print("starting local translation model...", file=sys.stderr, flush=True)
        return LlamaTranslator(cfg, glossary, models)
    raise ValueError(f"unknown translate.backend: {cfg.backend!r}")
