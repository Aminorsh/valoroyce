"""Command line: `valoroyce run | setup | devices | translate | say`."""

from __future__ import annotations

import argparse
import sys
import time

from .config import Config, load_config
from .glossary import Glossary


def _print_caption(c, show_source: bool = True) -> None:
    mark = " " if c.final else "~"
    src = f"  [{c.lang}] {c.source}" if show_source else ""
    print(f"{mark} #{c.utt:<3} {c.latency * 1000:5.0f}ms  {c.text}{src}", flush=True)


def _load(args) -> tuple[Config, Glossary]:
    cfg = load_config(args.config)
    return cfg, Glossary.load(cfg.glossary.extra, cfg.glossary.fuzzy)


def cmd_run(args) -> int:
    from .audio import open_source
    from .pipeline import Pipeline
    from .translate import make_translator

    cfg, glossary = _load(args)
    if args.no_overlay:
        cfg.overlay.enabled = False
    if cfg.overlay.enabled:
        from . import state

        state.apply(cfg)  # tray-menu choices from earlier runs
    if args.file:
        cfg.audio.source, cfg.audio.file = "file", args.file
    if args.fast:
        cfg.audio.realtime = False
    if args.device is not None:
        cfg.audio.device = args.device
    if args.backend:
        cfg.translate.backend = args.backend
    if args.cpu:
        cfg.translate.gpu = False

    if cfg.overlay.enabled:
        from .overlay import run_overlay

        return run_overlay(cfg, glossary, echo=_print_caption)

    source = open_source(cfg.audio)
    translator = make_translator(cfg.translate, glossary, cfg.models_path)
    try:
        pipeline = Pipeline(cfg, source, translator, glossary, _print_caption)
        pipeline.start()
        name = getattr(source, "name", cfg.audio.file)
        print(f"listening: {name}  (Ctrl+C to quit)", file=sys.stderr, flush=True)
        try:
            while not pipeline.done.wait(0.2):
                pass
        except KeyboardInterrupt:
            pass
        pipeline.stop()
        if pipeline.error:
            raise pipeline.error
        return 0
    finally:
        translator.close()


def cmd_setup(args) -> int:
    from .setup import run_setup

    cfg = load_config(args.config)
    return run_setup(cfg.models_path, mirror=args.mirror, gpu=args.gpu, skip_llm=args.no_llm)


def cmd_devices(args) -> int:
    from .audio import list_devices

    for kind, name, default in list_devices():
        print(f"{kind:9} {'*' if default else ' '} {name}")
    print('\n* = system default. Put part of a name in config.toml: [audio] device = "..."')
    return 0


def cmd_translate(args) -> int:
    """Translate typed text: checks the glossary and the model without audio."""
    from .translate import make_translator

    cfg, glossary = _load(args)
    if args.backend:
        cfg.translate.backend = args.backend
    translator = make_translator(cfg.translate, glossary, cfg.models_path)
    try:
        lines = args.text or [line.strip() for line in sys.stdin if line.strip()]
        for line in lines:
            fixed = glossary.correct(line)
            hits = glossary.find(fixed)
            t0 = time.monotonic()
            out = translator.translate(fixed, "", hits)
            ms = (time.monotonic() - t0) * 1000
            print(f"{ms:5.0f}ms  {out}    <- {fixed}")
    finally:
        translator.close()
    return 0


def cmd_say(args) -> int:
    """Chinese in, Japanese or English out: the message box without the window."""
    from .translate import make_translator

    cfg, glossary = _load(args)
    if args.backend:
        cfg.translate.backend = args.backend
    translator = make_translator(cfg.translate, glossary, cfg.models_path)
    try:
        lines = args.text or [line.strip() for line in sys.stdin if line.strip()]
        for line in lines:
            print(translator.compose(line, args.to or cfg.compose.target))
    finally:
        translator.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy codepage
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(prog="valoroyce", description=__doc__)
    p.add_argument("-c", "--config", help="config file (default: ./config.toml if present)")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="caption live audio (default)")
    r.add_argument("--file", help="caption a WAV file instead of live audio")
    r.add_argument("--fast", action="store_true", help="with --file: do not pace to real time")
    r.add_argument("--device", help="output/input device name (substring)")
    r.add_argument("--backend", choices=["llama", "openai", "none"], help="translation backend")
    r.add_argument("--cpu", action="store_true", help="keep the translation model off the GPU")
    r.add_argument("--no-overlay", action="store_true", help="print captions to the console only")
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("setup", help="download models and the llama.cpp runtime")
    s.add_argument("--mirror", choices=["auto", "global", "cn"], default="auto",
                   help="download source (cn = ModelScope / hf-mirror)")
    s.add_argument("--gpu", choices=["vulkan", "cuda", "cpu"], default="vulkan",
                   help="llama.cpp build for Windows (vulkan works on any modern GPU)")
    s.add_argument("--no-llm", action="store_true", help="skip the translation model")
    s.set_defaults(fn=cmd_setup)

    d = sub.add_parser("devices", help="list audio devices")
    d.set_defaults(fn=cmd_devices)

    t = sub.add_parser("translate", help="translate text from arguments or stdin")
    t.add_argument("text", nargs="*")
    t.add_argument("--backend", choices=["llama", "openai", "none"])
    t.set_defaults(fn=cmd_translate)

    y = sub.add_parser("say", help="translate your Chinese into Japanese / English")
    y.add_argument("text", nargs="*")
    y.add_argument("--to", choices=["ja", "en"], help="target language (default: compose.target)")
    y.add_argument("--backend", choices=["llama", "openai", "none"])
    y.set_defaults(fn=cmd_say)

    args = p.parse_args(argv)
    if not args.cmd:
        args = p.parse_args([*(argv if argv is not None else sys.argv[1:]), "run"])
    try:
        return args.fn(args)
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
