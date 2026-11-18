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
    from .fullspeed import full_speed
    from .pipeline import Pipeline
    from .translate import make_caption_translator

    full_speed()  # the game is in front; Windows would run us in its efficiency mode
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
    if args.app is not None:
        cfg.audio.app, cfg.audio.app_mode = args.app, "only"
    if args.except_app is not None:
        cfg.audio.app, cfg.audio.app_mode = args.except_app, "except"
    if args.backend:  # an explicit choice on the command line: that model alone
        cfg.translate.backend, cfg.translate.custom.backend = args.backend, ""
    if args.cpu:
        cfg.translate.gpu = False

    if cfg.overlay.enabled:
        from .overlay import run_overlay

        return run_overlay(cfg, glossary, echo=_print_caption, record=args.record)

    source = open_source(cfg.audio)
    translator = make_caption_translator(cfg.translate, glossary, cfg.models_path)
    recording = None
    try:
        pipeline = Pipeline(cfg, source, translator, glossary, _print_caption)
        name = getattr(source, "name", cfg.audio.file)
        if args.record:
            from .record import Recording, describe

            recording = pipeline.recording = Recording(header=describe(cfg, str(name)))
            print(f"recording to {recording.wav_path}", file=sys.stderr, flush=True)
        pipeline.start()
        print(f"listening: {name}, recognising with {pipeline.recognizer.name}  (Ctrl+C to quit)",
              file=sys.stderr, flush=True)
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
        if recording:
            recording.close()
            print(f"recording saved: {recording.wav_path} and {recording.log_path.name}",
                  file=sys.stderr)


def cmd_setup(args) -> int:
    from .setup import run_setup

    cfg = load_config(args.config)
    return run_setup(cfg.models_path, mirror=args.mirror, gpu=args.gpu, skip_llm=args.no_llm,
                     whisper=args.whisper)


def cmd_devices(args) -> int:
    from .audio import list_devices

    for kind, name, default in list_devices():
        print(f"{kind:9} {'*' if default else ' '} {name}")
    print('\n* = system default. Put part of a name in config.toml: [audio] device = "..."')
    try:
        from .apploop import playing, supported

        apps = playing() if supported() else []
    except OSError as e:
        print(f"\ncannot list programs: {e}")
        return 0
    if apps:
        print("\nPrograms with sound open (> = playing now):")
        for name, now in apps:
            print(f"  {'>' if now else ' '} {name}")
        print('Listen to one of them only: [audio] app = "name.exe" (or run --app name.exe);\n'
              'everything but it: app_mode = "except" (or run --except-app name.exe).')
    return 0


def cmd_translate(args) -> int:
    """Translate typed text: checks the glossary and the model without audio."""
    from .translate import make_caption_translator

    cfg, glossary = _load(args)
    if args.backend:
        cfg.translate.backend, cfg.translate.custom.backend = args.backend, ""
    translator = make_caption_translator(cfg.translate, glossary, cfg.models_path)
    try:
        lines = args.text or [line.strip() for line in sys.stdin if line.strip()]
        for line in lines:
            fixed = glossary.correct(line)
            hits = glossary.find(fixed)
            t0 = time.monotonic()
            out = translator.translate(fixed, "", hits)
            ms = (time.monotonic() - t0) * 1000
            why = getattr(translator, "fell_back", "")
            print(f"{ms:5.0f}ms  {out}    <- {fixed}" + (f"    (fallback: {why})" if why else ""))
    finally:
        translator.close()
    return 0


def cmd_say(args) -> int:
    """Chinese in, Japanese or English out: the message box without the window."""
    from .translate import make_compose_translator, make_translator

    cfg, glossary = _load(args)
    if args.backend:  # an explicit choice on the command line wins over [compose]
        cfg.translate.backend, cfg.compose.backend = args.backend, ""
    started = []  # the caption translator, once something needs it

    def caption_translator():
        if not started:
            started.append(make_translator(cfg.translate, glossary, cfg.models_path))
        return started[0]

    translator = (make_compose_translator(cfg, glossary, cfg.models_path, caption_translator)
                  or caption_translator())
    try:
        lines = args.text or [line.strip() for line in sys.stdin if line.strip()]
        for line in lines:
            print(translator.compose(line, args.to or cfg.compose.target))
    finally:
        translator.close()
        if started and started[0] is not translator:
            started[0].close()
    return 0


def cmd_quit(args) -> int:
    from PySide6.QtCore import QCoreApplication

    from .desktop import SingleInstance

    _app = QCoreApplication.instance() or QCoreApplication([])
    if SingleInstance.ask_running("quit"):
        print("asked the running copy to quit")
        return 0
    print("Valoroyce is not running")
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
    r.add_argument("--app", help="listen to this program only, by file name (see `devices`)")
    r.add_argument("--except-app", help="listen to everything except this program")
    r.add_argument("--backend", choices=["llama", "openai", "none"], help="translation backend")
    r.add_argument("--cpu", action="store_true", help="keep the translation model off the GPU")
    r.add_argument("--no-overlay", action="store_true", help="print captions to the console only")
    r.add_argument("--record", action="store_true",
                   help="save what is heard and the captions shown to ./recordings (for problem reports)")
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("setup", help="download models and the llama.cpp runtime")
    s.add_argument("--mirror", choices=["auto", "global", "cn"], default="auto",
                   help="download source (cn = ModelScope / hf-mirror)")
    s.add_argument("--gpu", choices=["vulkan", "cuda", "cpu"], default="vulkan",
                   help="llama.cpp build for Windows (vulkan works on any modern GPU)")
    s.add_argument("--no-llm", action="store_true", help="skip the translation model")
    s.add_argument("--whisper", action="store_true",
                   help="also fetch Whisper (1.6 GB): accurate recognition on an NVIDIA graphics card")
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

    q = sub.add_parser("quit", help="end the running copy (its tray menu's 退出)")
    q.set_defaults(fn=cmd_quit)

    args = p.parse_args(argv)
    if not args.cmd:
        args = p.parse_args([*(argv if argv is not None else sys.argv[1:]), "run"])
    try:
        return args.fn(args)
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _end(code: int) -> None:
    """End the process at once, after our own cleanup. With Whisper on the
    graphics card and the caption window, the libraries' own teardown crashes
    the process (0xC0000409 in Python's shutdown, 0xC0000005 in the exit
    routines of the loaded DLLs, which even os._exit runs). Our files are
    closed by now, so on Windows the process is ended without running them."""
    import os

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (AttributeError, OSError, ValueError):
            pass
    if sys.platform == "win32":
        import ctypes

        try:
            kernel32 = ctypes.WinDLL("kernel32")
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.TerminateProcess(ctypes.c_void_p(kernel32.GetCurrentProcess()),
                                      ctypes.c_uint(code & 0xFFFFFFFF))
        except (AttributeError, OSError):
            pass
    os._exit(code)


def entry() -> None:
    """The `valoroyce` command."""
    _end(main())


def gui() -> None:
    """`valoroyce-gui`: the same program started without a console window, as
    from a shortcut. Output goes to valoroyce.log, errors to a message box."""
    from .desktop import log_to_file, message_box

    try:
        log = log_to_file()
    except OSError as e:
        message_box("Valoroyce 无法启动",
                    f"无法写入日志文件：{e}\n\n请把 Valoroyce 放在你有写入权限的文件夹里（例如“文档”），再启动一次。")
        _end(1)
    print(f"--- start {time.strftime('%Y-%m-%d %H:%M:%S')} ---", flush=True)
    import faulthandler

    faulthandler.enable(sys.stderr)  # a crash in a library leaves its trace in the log
    try:
        code = main(sys.argv[1:] or ["run"])
    except SystemExit as e:  # argparse
        code = e.code if isinstance(e.code, int) else 1
    except BaseException:
        import traceback

        traceback.print_exc()
        code = 1
    if code:
        message_box("Valoroyce 出错了",
                    f"程序因为错误退出了。详细信息在这个文件里：\n{log.resolve()}\n\n"
                    "最后几行通常写着原因和该怎么做。如果看不懂，把这个文件发给开发者。")
    _end(code)


if __name__ == "__main__":
    entry()
