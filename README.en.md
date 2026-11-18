<img src="src/valoroyce/data/icon.png" width="88" align="right" alt="Valoroyce icon">

# Valoroyce

[中文](README.md) | English

**Offline** live captions for VALORANT voice chat. It listens to Japanese or English speech and puts Chinese subtitles on screen, so playing on a foreign server feels like watching a subtitled video.

```
what the PC is playing ──> split into ──> speech ──────> term ────────> local ─────────> on-screen
  (system loopback)        sentences      recognition    correction     translation      captions
                           (Silero VAD)   (Whisper or    (glossary)     (Hy-MT2 1.8B)
                                           SenseVoice)
```

- **Fully local.** Once the models are downloaded it works with the network unplugged. No account, no fees, nothing uploaded.
- **Knows the game's vocabulary.** A built-in glossary maps agents, weapons and maps to the names used in the mainland-China client (`Iso` / `アイソ` → 壹决, `ヴァンダル` → 狂徒), plus common callouts. Near-miss transcriptions are snapped to the right name. The glossary is a text file you can extend.
- **Captions while they are still talking.** A half-sentence appears first and is replaced by the final line.
- **Does not touch the game.** It records system audio and draws an ordinary always-on-top transparent window. No memory reading, no injection, no modified game files: the same class of program as OBS or a desktop lyrics widget.

> Status: early. Live capture, the caption window and the settings window have run on one Windows 11 PC (laptop RTX 4060). Recognition in real matches is still being improved; several people talking at once, or loud background music, remain unreliable. Bug reports are welcome.

The target language is Chinese because that is who the project was built for. The translation target and the glossary are data, not code, so other targets are possible; see [Contributing](#contributing).

## Requirements

- Windows 10 / 11. Linux and macOS run it too, mainly for development.
- An **NVIDIA graphics card** is best: speech is then recognised with Whisper, which is far more accurate (about 1.3 GB of VRAM), and translation is faster too. Without one it still works: recognition falls back to SenseVoice on the CPU, which is clearly less accurate.
- Disk space: about 6 GB with an NVIDIA card, about 1.5 GB otherwise.
- The game set to **Windowed Fullscreen**. In exclusive fullscreen no overlay window can be shown.

## Install

**Windows (recommended):** download `Valoroyce-Setup-….exe` from [Releases](https://github.com/Aminorsh/valoroyce/releases) and run it.

- The installer is not signed, so Windows may say "Windows protected your PC": click "More info", then "Run anyway".
- No administrator rights are needed. It installs to `%LOCALAPPDATA%\Programs\Valoroyce` unless you pick another folder.
- The installer asks which models to download: Whisper with an NVIDIA card, SenseVoice without one, plus the translation model Hy-MT2. You may tick none and download them later from the settings window.
- The first start downloads the runtime (about 115 MB) in a window with a progress bar, speed and time left. Then the program opens and fetches the models you picked (about 4 GB with an NVIDIA card, about 1.4 GB otherwise), from whichever source is fastest. Captions start by themselves when that is done, and from then on it works offline. Downloads can be cancelled and continued later.
- **Updating**: run the newer installer. It closes a running Valoroyce first and keeps your models and settings.
- **Uninstalling**: Windows Settings → Apps → "Valoroyce 实时字幕". It asks whether to delete the models, settings and recordings too.
- If something goes wrong, `valoroyce.log` in the install folder holds the program's output. Please attach it to an issue.

From source (for development):

1. Install [uv](https://docs.astral.sh/uv/), a Python manager. In PowerShell:

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

2. Get this project: `git clone`, or Code → Download ZIP on GitHub and unpack it.
3. Double-click `start.bat`. The first run installs dependencies, then the models are downloaded from the settings window. Later runs start directly.

By hand, on any system:

```sh
uv sync --extra app        # install dependencies; add --extra whisper for Whisper (about 1.3 GB of GPU libraries)
uv run valoroyce setup     # models and llama.cpp; add --whisper for the Whisper model
uv run valoroyce           # start (opens the settings window if a model is missing)
```

## Using it

A caption strip appears near the bottom of the screen, and a red speech-bubble icon appears in the system tray (possibly under "^ Show hidden icons"; drag it out to pin it). Click the icon for the menu. The menu itself is in Chinese:

| Menu item | Meaning |
|---|---|
| 设置与模型… | Open the settings window (next section) |
| 暂停字幕 | Pause captions; click again to resume |
| 调整位置 | Move mode: the strip becomes a draggable blue box. Untick when done |
| 显示原文 | Also show the recognised Japanese / English above the Chinese |
| 字号 | Font size: small / medium / large / extra large |
| 翻译运行在 | Run translation on the **GPU** (faster) or the **CPU** (leaves the GPU to the game). Takes effect in a few seconds |
| 声音来源 | Which playback device to listen to (see the next section) |
| 退出 | Quit |

### The settings window

"设置与模型…" in the tray menu opens it. A change is remembered at once; changes that need models reloaded take effect when you click "应用更改" (apply) at the bottom or close the window (a few seconds).

| Page | What it does |
|---|---|
| 模型 (models) | One card per model (and one for your own caption translation model): what it is for, the hardware it suits, its size and its state. **下载 / 取消 / 继续下载** (download, cancel, continue), **删除** (delete: frees the disk space, can be downloaded again any time), and untick **启用** (enabled) to keep the files but not use the model. Recognition models have ▲ ▼ to set the **order of use**: they are tried from the top and the first that can run is used. The download source is chosen at the top (fastest by test / mirrors in China / international) |
| 字幕 (captions) | Font size, rows on screen, how long a caption stays, background opacity, width, original text on or off, dragging the strip |
| 声音与识别 (sound and recognition) | Playback device to listen to (see "Caption only your teammates"), which programs to listen to (see "When you talk over a separate voice-call program"), languages, captions while someone is still talking, the pause that ends a sentence |
| 翻译 (translation) | Translation model on the GPU or the CPU, whether half-sentences are translated; your own translation model (see "Captions on a translation model of your choice") |
| 发消息 (messages) | Target language, shortcut, an online service instead of the local model (address, model name, API key) |

What is changed in the settings window or the tray menu is stored in `valoroyce.state.json` and overrides `config.toml`. Delete that file (or click "恢复默认设置" on the 关于 page) to return to the configured defaults.

### Sending text messages to teammates

To type to your team: choose "发消息（中文 → 日语 / 英语）…" in the tray menu, or press **Ctrl+Alt+T**. A small input box opens. Type Chinese and press Enter; the translation is shown and **copied to the clipboard**. Go back to the game's chat box, press Ctrl+V and send. Esc closes the box.

- Pick 日语 (Japanese) or English in the box; the choice is remembered.
- Agents, weapons, maps and callouts are replaced with what the other language calls them. Chinese nicknames and shorthand are understood too (保安 → Cypher, 奶妈 → Sage, A大 → A main, 有大 → "has ult"). Captions always show the official names.
- The program only copies. It never types into the game or sends anything for you.
- Machine translation does best with short, direct sentences. Jokes and slang tend to go wrong.

#### Better messages: a separate model for the message box (optional)

By default, messages use the same small local model as the captions: fully offline, but stiff with casual speech and slang. You can give the message box a stronger model of its own. **Captions are not affected** (for captions, see the next section). Add one block to `config.toml`:

**An online service** (anything that speaks OpenAI's API; you need your own API key). The easy way: on the 发消息 page of the settings window choose "在线服务" and fill in the address, model name and key (a button fills in DeepSeek's values). Or in `config.toml`, DeepSeek shown here:

```toml
[compose]
backend = "openai"
base_url = "https://api.deepseek.com"
model = "deepseek-flash"
api_key = "sk-your-key"
extra_body = { thinking = { type = "disabled" } }   # no "thinking": it is slow and can return nothing
```

- Only the sentence you type into the box is sent to the service. Teammates' voices and the captions never are.
- It needs a network connection and is billed by the provider (a message is a few dozen tokens). About one second per message.
- Online services may decline some sentences (political content, heavy profanity). When the service declines, is unreachable, or rejects the key, that one message is **translated by the local caption model instead**, and the box says so.
- A message containing a word listed in `local_only` is never sent to the service; the local model translates it. The default list has one entry (see `config.example.toml`). Add any word the service handles badly: `local_only = ["word1", "word2"]`.
- `config.toml` and `valoroyce.state.json` (where the settings window keeps the key) are ignored by git, but do not share a file that contains your key. The key can also be given as the environment variable `VALOROYCE_COMPOSE_API_KEY`.

**A second local model** (still fully offline): put a GGUF chat model (for example Qwen3.5-4B, Q4_K_M, about 2.7 GB) into the `models` folder, then:

```toml
[compose]
backend = "llama"
model_file = "Qwen3.5-4B-Q4_K_M.gguf"
```

It is loaded the first time the message box opens. Its tone is more natural than the default model's, but it is less accurate than a large online model and needs extra RAM or VRAM.

### Captions on a translation model of your choice (optional)

The default caption model, Hy-MT2, is small, fast and fully offline, but casual speech, slang and half sentences often come out stiff, and sometimes it copies the original. You can have captions **go to a model of your own choice first**, with Hy-MT2 behind it:

- On the 模型 page of the settings window tick **启用** (enabled) on "自定义翻译模型" (own translation model), then choose its kind and fill in the address and model name on the 翻译 page. Buttons fill in Ollama, LM Studio and DeepSeek.
- **A model server on your PC** (Ollama, LM Studio, ...: choose "兼容 OpenAI 接口的服务") or **a .gguf file of your own** (put it in the `models` folder and choose "自己的 .gguf 模型文件"; the program starts it): still fully offline, but it takes VRAM next to Hy-MT2.
- **An online service** (DeepSeek, for example, with your own API key): clearly more natural. Two things to know, which the settings window also says:
  - **Every sentence** your teammates say (the recognised text) is sent to that service; it is no longer handled only on your PC. This is off by default.
  - Some online services **silently alter sentences** on some topics, sometimes into the opposite meaning, and nothing on screen shows it.
- A sentence it fails on, declines, or has not finished within the timeout (2.5 s by default) is **translated by Hy-MT2 instead**. After failures in a row it is left alone for 30 seconds, and the screen says so once. With Hy-MT2 switched off, those sentences get glossary substitution only.
- Half sentences while someone is still talking stay on Hy-MT2 by default (fast, and free); only finished sentences are sent. So a finished caption arrives about a second later than with Hy-MT2 alone. Measured on a recorded match with DeepSeek: about 1 s per sentence; finished captions appeared a median 2.1 s after the speaker stopped (about 1 s with Hy-MT2 alone).
- To configure it by hand see `[translate.custom]` in `config.example.toml`; the key can also come from the environment variable `VALOROYCE_CAPTION_API_KEY`.

### GPU or CPU?

The translation model runs on the GPU by default, which gives faster captions but shares the card with the game (about 1.5 GB of VRAM, plus about 1.3 GB when Whisper recognises). If the game stutters with captions on, switch translation to the CPU in the tray menu ("翻译运行在") or on the 翻译 page of the settings window. On CPU, a short line is finalised in roughly a second. On a card with only 4 GB, start with this setting.

## Caption only your teammates (strongly recommended)

By default it hears **everything** the PC plays, so gunfire, ability sounds and agent voice lines are mixed in, and the agent lines get captioned too. Send voice chat to its own playback device and the captions follow only your teammates:

1. In game: Settings → Audio → Voice Chat → **Output Device**. Pick a device different from the one playing game sound.
   - Some headsets expose separate "Game" and "Chat" devices. Pick Chat.
   - With a single device, install a virtual sound card such as VB-CABLE, send voice chat to it, and in Windows sound settings enable "Listen to this device" on it so the audio also reaches your headset.
2. Pick that device under "声音来源" in the tray menu or on the 声音与识别 page of the settings window. (Or put it in the config: `[audio] device = "CABLE"`. Part of the name is enough; `uv run valoroyce devices` lists the names.)

## When you talk over a separate voice-call program

If you and your friends chat in Chinese over QQ, KOOK, Discord or similar, that speech is recognised and translated too by default. On the 声音与识别 page of the settings window, set 听哪些程序 to 除一个程序以外的全部程序 (everything except one program) and pick the call program under 程序 (make it play a sound first, then press 刷新). The captions then ignore your chat. 只听一个程序 does the opposite. This is the same Windows feature as OBS's Application Audio Capture: no administrator rights, nothing changes in what you hear, and the game is not touched. It needs Windows 10 version 2004 or later. In the config: `[audio] app = "KOOK.exe"`, `app_mode = "except"`.

Reports online say VALORANT's team voice is played by the Riot Client (`RiotClientServices.exe`), not by the game itself. If so, listening to that program alone leaves only your teammates, without gunfire or agent lines. This has not been checked in a match yet.

## Configuration

The common options are all in the settings window; no file needs editing. To write the configuration by hand (or for options the window does not have), copy `config.example.toml` to `config.toml` and edit. Every key is optional. Note that a value changed in the settings window overrides `config.toml`. The ones people change most:

| To do this | Set |
|---|---|
| Order in which recognition models are tried | `[asr] order = ["whisper", "sensevoice"]` (one left out is never used) |
| Expect Japanese only (more stable recognition) | `[asr] languages = ["ja"]` |
| Faster captions, or fewer chopped sentences | `[vad] min_silence = 0.25` (faster) or `0.5` (fewer fragments) |
| Font size, how long captions stay, position | `[overlay] font_size / linger / y` |
| Show the original text too | `[overlay] show_source = true` |
| Keep translation off the GPU | `[translate] gpu = false` (or use the tray menu) |
| No GPU and a busy CPU | `[translate] partials = false` (translate finished sentences only) |
| Default language and shortcut for messages | `[compose] target = "en"`, `hotkey = "ctrl+shift+f8"` (empty = no shortcut) |
| A stronger model for messages (online or local) | `[compose] backend = "openai"` or `"llama"`, see "Better messages" above |
| Captions on your own model first, Hy-MT2 behind it | `[translate.custom] backend = "openai"` (or `"llama"`), see "Captions on a translation model of your choice" |
| Captions on your own model server only (Ollama, LM Studio, ...), no Hy-MT2 | `[translate] backend = "openai"`, plus `base_url`, `model`, `prompt = "generic"` |
| No translation model, glossary substitution only | `[translate] backend = "none"` |

### Adding your own terms

Create a file such as `my_terms.toml`:

```toml
terms = [
  { zh = "壹决", kind = "agent", en = ["Iso"], ja = ["アイソ"], mishear = ["愛想"] },
  { zh = "白给", kind = "callout", ja = ["無駄死に"] },
]
```

and list it in `config.toml`: `[glossary] extra = ["my_terms.toml"]`.

- `kind = "agent" | "weapon" | "map"`: a name. It is always shown exactly as you wrote it.
- `kind = "callout"`: slang. It is only offered to the translation model as a hint.
- `mishear`: wrong transcriptions the recogniser tends to produce for this term.
- `zh_alias`: other Chinese ways of saying it, such as nicknames (`zh_alias = ["保安"]`). Recognised when translating your own messages.

The built-in list is [`src/valoroyce/data/valorant.toml`](src/valoroyce/data/valorant.toml).

## Latency

Measured on a 16-core Linux machine with no GPU (translation on the CPU, 4 threads):

- While someone is talking, a half-sentence caption refreshes about every 0.5 s, each one 0.2–1.3 s behind the audio.
- After they stop: about 0.8–1.0 s for a short line, about 2 s for a long one (5 seconds of speech). Of that, 0.35 s is the silence needed to decide the sentence has ended.

With 8 translation threads, short lines were finalised in about 0.65–0.7 s. Translation is most of the delay and should be clearly faster on a GPU, but we have no GPU machine to measure it on yet.

## Commands

```sh
uv run valoroyce devices                      # list audio devices and programs playing sound
uv run valoroyce run --file some.wav          # caption a recording
uv run valoroyce run --no-overlay             # no window; print captions to the terminal
uv run valoroyce run --cpu                    # keep translation off the GPU for this run
uv run valoroyce translate "アイソがロングにいる"   # try the glossary and the model on text
uv run valoroyce say --to en "保安在B点"        # Chinese -> Japanese / English
uv run valoroyce quit                         # close a running Valoroyce (and the translation server it started)
uv run --extra dev pytest                     # run the tests
```

A copy installed with the installer has no `uv run`: use `.venv\Scripts\valoroyce.exe` in the install folder instead of `uv run valoroyce`.

## Known limitations

- Agent voice lines and ability call-outs are treated as speech (see "Caption only your teammates").
- Several people talking at once are merged into one line. Speakers are not told apart yet.
- Half-sentence captions are rewritten as the speaker continues and can jump around.
- Captions are not filtered. Insults, swearing and sensitive topics are translated as said (strong language may come out slightly milder than the original).
- Game terms are fixed mostly after recognition, so unusual phrasings can still be misheard. If you see one, tell us what was heard and what was said; adding it to the glossary fixes it.

### When captions are wrong: send a recording

For wrong recognition, wrong translation or sentences glued together, the most useful report is a recording. Tick "录音用于反馈问题" (record for a problem report) in the tray menu, let the problem happen for a while, and click it again to stop (10 minutes at most, then it stops by itself). Two files with the same name appear in the `recordings` folder, which is opened for you:

- `.wav`: the sound the program heard, exactly as it was given to the recogniser
- `.txt`: every caption that was shown (text heard, translation, time)

Recordings stay on your computer; nothing is uploaded. They contain other people's voices, so sharing them is your decision. From the command line: `uv run valoroyce run --record`.

## Contributing

Help is welcome, especially from anyone who can run it on Windows with the game.

### Development setup

```sh
git clone https://github.com/Aminorsh/valoroyce && cd valoroyce
uv sync --extra app --extra dev
uv run valoroyce setup          # optional: models, needed for the end-to-end tests
uv run pytest
```

Without models the end-to-end tests skip themselves and the rest still run. Development does not need Windows, a GPU or a sound card: `--file some.wav` feeds a recording through the same pipeline, and `QT_QPA_PLATFORM=offscreen` runs the window code headless.

### How the code is laid out

Everything is in `src/valoroyce/`. Each stage lives in one file and knows nothing about the others except through `pipeline.py`.

| File | Job |
|---|---|
| `audio.py` | Audio sources (loopback, one program or all but one, microphone, WAV file) yielding 16 kHz mono blocks; resampler |
| `apploop.py` | Windows application loopback in plain ctypes: the sound of one program (or of all but one), the list of programs playing sound |
| `speech.py` | Sentence splitting (Silero VAD) and the two recognisers: Whisper on an NVIDIA card (faster-whisper), SenseVoice on the CPU (sherpa-onnx) |
| `listener.py` | What counts as finished speech and what is shown while someone is still talking |
| `glossary.py` | Term handling: fix the transcript, list the terms in a sentence, enforce names in the output |
| `data/valorant.toml` | The built-in glossary |
| `translate.py` | Translation backends, the prompt formats, and the llama.cpp child process |
| `pipeline.py` | Threads and queues tying the stages together; decides which captions are still worth translating |
| `overlay.py` | The caption window, the tray menu, and restarting the pipeline when a menu choice changes |
| `compose.py` | The message box: Chinese in, Japanese or English out to the clipboard |
| `record.py` | Opt-in recording for problem reports: the audio as heard plus the captions shown |
| `hotkey.py` | The system-wide shortcut that opens the message box (Windows) |
| `settings.py` | The settings window: models page, captions, sound, translation, messages |
| `catalog.py` | The models and runtimes a user can download, switch off or remove; what a PC needs |
| `state.py` | Choices made in the settings window and the tray menu, remembered between runs |
| `config.py` | All settings and their defaults |
| `setup.py` | Model and runtime downloads with source speed-testing and checksums |
| `cli.py` | The `valoroyce` command |

### Ground rules

- **Offline by default.** The default path must work with no network once models are present. Online services may only be opt-in configuration.
- **Never touch the game.** Audio comes from OS loopback capture and captions go to an ordinary window. Outgoing messages stop at the clipboard; nothing sends keystrokes to the game. Nothing that reads, hooks or injects into the game process will be accepted; players' accounts are at stake.
- **Defaults live only in `config.py`.** `config.example.toml` must show the same values, and a test checks that it does.
- **Say what was tested where.** Much of this has only run on Linux. If you verify something on Windows, say so in the pull request.
- **Keep test data clean.** Do not put offensive or politically sensitive sentences in the repository, including tests, even to test how they are handled.

### Good places to start

- **Glossary.** Missing or wrong terms, callouts for individual maps, real mishearings you have seen. This needs no programming: edit `data/valorant.toml`. Place names currently have plain Chinese renderings that have not been checked against the Chinese client's own labels.
- **Windows testing.** Loopback capture, the caption window over the game, the tray menu, `start.bat`, GPU builds of llama.cpp. Reports of what works are as useful as fixes.
- **Latency.** Measurements on real GPUs; cancelling a half-sentence translation when the final sentence arrives.
- **Recognition of game terms.** Matching by sound rather than spelling, so a name misheard as an ordinary word can still be recovered.
- **Telling speakers apart.** Voice fingerprints per sentence to colour captions by player.
- **Other target languages.** `[translate] target` already selects the language the model translates into. What is missing is a glossary for that language (the `zh` field is the Chinese name) and a translated tray menu.
- **Packaging.** A release that does not require installing uv.

## Credits and licences

The code is released under the [MIT](LICENSE) licence. Components downloaded at run time have their own:
[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) (Apache-2.0),
[SenseVoice](https://github.com/FunAudioLLM/SenseVoice) (see its repository for the model licence),
[Whisper large-v3-turbo](https://github.com/openai/whisper) (MIT) with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and [CTranslate2](https://github.com/OpenNMT/CTranslate2) (MIT),
[Silero VAD](https://github.com/snakers4/silero-vad) (MIT),
[Hy-MT2](https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF) (Apache-2.0),
[llama.cpp](https://github.com/ggml-org/llama.cpp) (MIT).

This project is not affiliated with Riot Games. VALORANT and related names are trademarks of Riot Games.
