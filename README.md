<img src="src/valoroyce/data/icon.png" width="88" align="right" alt="Valoroyce icon">

# Valoroyce

中文 | [English](README.en.md)

无畏契约（VALORANT）队内语音的**离线**实时字幕：听日语 / 英语，在屏幕上显示中文，像看带字幕的视频一样打外服。

```
你的电脑正在播放的声音 ──> 切句 ──> 语音识别 ──> 术语纠正 ──> 本地翻译模型 ──> 屏幕字幕
   (系统回环录音)      (Silero VAD) (SenseVoice)  (内置瓦罗兰特词表)   (Hy-MT2 1.8B)
```

- **完全本地**：模型下载完之后可以断网使用，不需要账号、不收费。
- **懂游戏黑话**：内置英雄 / 武器 / 地图的国服译名（`Iso`、`アイソ` → 壹决，`ヴァンダル` → 狂徒），以及常用报点和术语；识别错的近似写法会被自动纠正。词表是一个文本文件，可以自己加。
- **边说边出字幕**：对方还没说完就先显示半句，说完后定稿。
- **不碰游戏**：只是录系统声音加一个置顶的透明窗口，不读内存、不注入、不改游戏文件，和 OBS 录屏、歌词悬浮窗是同一类东西。

> 状态：v0.1，早期版本。核心流程已在 Linux 上用音频文件跑通；**Windows 上的实时录音和悬浮窗还没有在真机实测**，遇到问题请提 issue。

## 需要什么

- Windows 10 / 11（Linux、macOS 也能跑，主要用于开发）
- 约 1.7 GB 磁盘空间放模型
- 有独立显卡最好（翻译模型用显卡跑更快），没有也能用 CPU 跑
- 游戏显示模式设为 **「窗口全屏」**。独占全屏下任何悬浮窗都显示不出来

## 安装

**最简单的方式（Windows）**：到 [Releases](https://github.com/Aminorsh/valoroyce/releases) 下载 `valoroyce-…-windows.zip`，解压到一个路径里没有中文的文件夹，双击 `start.bat`。第一次启动会自动下载运行环境和模型（共约 2 GB，需要联网），之后可以离线使用。

从源码安装：

1. 安装 [uv](https://docs.astral.sh/uv/)（一个 Python 管理工具）。打开 PowerShell 粘贴：

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

2. 下载本项目（`git clone`，或在 GitHub 页面点 Code → Download ZIP 并解压）。
3. 双击 `start.bat`。第一次会自动安装依赖并下载模型（约 1.4 GB，会自动测速选最快的下载源），之后直接启动。

手动方式（任何系统）：

```sh
uv sync --extra app        # 安装依赖
uv run valoroyce setup     # 下载模型和 llama.cpp；N 卡可加 --gpu cuda，无独显用 --gpu cpu
uv run valoroyce           # 启动
```

## 使用

启动后屏幕下方出现字幕条，同时任务栏右下角的系统托盘里会多一个红色的对话气泡图标（可能收在「^ 显示隐藏的图标」里，可以把它拖出来固定在任务栏上）。点击图标打开菜单：

| 菜单项 | 作用 |
|---|---|
| 暂停字幕 | 临时关掉字幕，再点一次恢复 |
| 调整位置 | 勾选后字幕条变成可拖动的蓝色框，拖到合适位置再取消勾选 |
| 显示原文 | 在中文上方同时显示识别出的日语 / 英语 |
| 字号 | 小 / 中 / 大 / 特大 |
| 翻译运行在 | **显卡**（更快）或 **CPU**（不占用显卡）。切换后几秒内生效 |
| 声音来源 | 选择要听哪个播放设备（见下一节） |
| 退出 | 关闭程序 |

菜单里选过的东西会记住，下次启动沿用（保存在 `valoroyce.state.json`，删掉它即恢复默认）。

### 给队友发文字消息

想打字和队友交流时：在托盘菜单点「发消息（中文 → 日语 / 英语）…」，或按快捷键 **Ctrl+Alt+T**，会弹出一个小输入框。输入中文按回车，译文会显示出来并**自动复制到剪贴板**，回到游戏的聊天框按 Ctrl+V 粘贴、回车发送即可。按 Esc 关闭输入框。

- 目标语言在输入框里选「日语」或「English」，会记住上次的选择。
- 英雄、武器、地图和报点会换成对方语言里的叫法，中文里的俗称和省略说法也认识（「保安」→ Cypher，「奶妈」→ Sage，「A大」→ A main，「有大」→ 有大招）。字幕里显示的始终是正式译名。
- 程序只负责复制，不会替你往游戏里打字或发送。
- 机器翻译对简短直接的句子效果最好（「壹决残血，在B长廊」），俏皮话和网络梗容易翻歪。

#### 想翻得更地道：给「发消息」单独换一个模型（可选）

默认情况下发消息和字幕共用同一个本地小模型，完全离线，但口语和梗翻得比较生硬。可以只给「发消息」换一个更强的模型，**字幕不受影响，始终在本地翻译**。在 `config.toml` 里加一段即可，两种方式任选：

**在线服务**（任何兼容 OpenAI 接口的服务都行，下面以 DeepSeek 为例，需要自己申请 API key）：

```toml
[compose]
backend = "openai"
base_url = "https://api.deepseek.com"
model = "deepseek-flash"
api_key = "sk-你的key"
extra_body = { thinking = { type = "disabled" } }   # 关掉“思考”，否则又慢又可能返回空
```

- 只有你在输入框里打的那句话会发给这个服务；队友的语音、字幕都不会。
- 需要联网，按服务商的价格计费（一句话很短，花费极少）。每句大约 1 秒。
- 在线服务可能拒绝翻译某些句子（比如涉及政治的内容或很重的脏话）。遇到拒绝、断网或 key 失效时，这一句会**自动改用本地的字幕模型**翻译，输入框下方会提示。
- 包含 `local_only` 里词语的句子不会发给在线服务，直接由本地模型翻译。默认只有一个词（见 `config.example.toml`），哪些词让在线服务翻得不对，自己加进去即可：`local_only = ["词1", "词2"]`。
- `config.toml` 不会被提交到 git，但也别把带 key 的配置文件发给别人。

**本地的第二个模型**（仍然完全离线）：把一个 GGUF 格式的聊天模型（例如 Qwen3.5-4B 的 Q4_K_M 版，约 2.7 GB）放进 `models` 文件夹，然后：

```toml
[compose]
backend = "llama"
model_file = "Qwen3.5-4B-Q4_K_M.gguf"
```

它会在第一次打开输入框时才加载。语气比默认模型自然，但准确度不如在线的大模型，而且会多占内存或显存。

### 显卡还是 CPU？

翻译模型默认放在显卡上跑，出字幕更快，但它会和游戏共用显卡（约占 1.5 GB 显存）。如果开着字幕时游戏掉帧或卡顿，在托盘菜单把「翻译运行在」切到 **CPU** 即可；CPU 模式下短句定稿大约 1 秒。

## 只翻译队友语音（强烈建议）

默认录的是电脑播放的**全部**声音，所以枪声、技能音效、英雄台词都会混进来，英雄台词也会被翻译。把语音聊天单独分到一个播放设备上，字幕就只跟队友的声音走：

1. 游戏内 设置 → 音频 → 语音聊天 → **输出设备**，选一个和游戏声音不同的设备。
   - 有些耳机自带「Game / Chat」两个设备，直接选 Chat 那个。
   - 只有一个设备的话，装一个虚拟声卡（如 VB-CABLE），把语音输出选成它，再在 Windows 声音设置里对这个虚拟设备勾选「侦听此设备」，转回你的耳机。
2. 在托盘菜单的「声音来源」里选中这个设备。（也可以写进配置文件：`[audio] device = "CABLE"`，填设备名的一部分即可，`uv run valoroyce devices` 可以列出设备名。）

## 配置

把 `config.example.toml` 复制为 `config.toml` 再改，所有项都可以不写。常用的几个：

| 想做的事 | 配置 |
|---|---|
| 只听日语（识别更稳） | `[asr] languages = ["ja"]` |
| 字幕出得更快 / 更完整 | `[vad] min_silence = 0.25`（更快）或 `0.5`（少把一句话切碎） |
| 字号、停留时间、位置 | `[overlay] font_size / linger / y` |
| 同时显示日语原文 | `[overlay] show_source = true` |
| 翻译不用显卡 | `[translate] gpu = false`（或托盘菜单切换） |
| 没有显卡，CPU 吃紧 | `[translate] partials = false`（只翻译说完的整句） |
| 发消息的默认语言、快捷键 | `[compose] target = "en"`、`hotkey = "ctrl+shift+f8"`（留空则不注册快捷键） |
| 发消息用更强的模型（在线或本地） | `[compose] backend = "openai"` 或 `"llama"`，见上文「想翻得更地道」 |
| 用自己的模型服务（Ollama / LM Studio 等） | `[translate] backend = "openai"`，并设置 `base_url`、`model`、`prompt = "generic"` |
| 不要翻译模型，只替换术语 | `[translate] backend = "none"` |

### 自己加术语

新建一个文件，例如 `my_terms.toml`：

```toml
terms = [
  { zh = "壹决", kind = "agent", en = ["Iso"], ja = ["アイソ"], mishear = ["愛想"] },
  { zh = "白给", kind = "callout", ja = ["無駄死に"] },
]
```

然后在 `config.toml` 里 `[glossary] extra = ["my_terms.toml"]`。`kind` 为 `agent / weapon / map` 的词一定会按你写的译名显示；`callout` 只是给翻译模型的参考。`mishear` 填识别器常写错的样子；`zh_alias` 填中文里的其他叫法（如 `zh_alias = ["保安"]`），发消息时会认出来。内置词表在 [`src/valoroyce/data/valorant.toml`](src/valoroyce/data/valorant.toml)，欢迎提 PR 补充。

## 延迟

在一台没有显卡的 16 核 Linux 机器上实测（翻译模型纯 CPU、4 线程）：

- 对方说话过程中，约每 0.5 秒刷新一次半句字幕，每次从收到声音到出字 0.2–1.3 秒；
- 说完之后的定稿字幕：短句约 0.8–1.0 秒，长句（5 秒、三十多个字）约 2 秒。其中 0.35 秒是用来判断「这句说完了」的静音等待。

把翻译线程数从 4 调到 8 后，短句定稿降到约 0.65–0.7 秒。翻译占了延迟的大头，用显卡跑预计会明显更快，但我们手头没有带显卡的机器，具体数字尚未实测。

## 常用命令

```sh
uv run valoroyce devices                      # 列出音频设备
uv run valoroyce run --file some.wav          # 用录音文件测试
uv run valoroyce run --no-overlay             # 不开悬浮窗，字幕打印在终端
uv run valoroyce run --cpu                    # 本次运行不使用显卡
uv run valoroyce run --record                 # 把听到的声音和字幕存到 recordings/，反馈问题用
uv run valoroyce translate "アイソがロングにいる"   # 直接测试术语和翻译
uv run valoroyce say --to en "保安在B点"        # 中文 → 日语 / 英语
uv run --extra dev pytest                     # 跑测试
```

## 已知问题

- 英雄台词、技能语音会被当成人说话（见上面「只翻译队友语音」）。
- 几个人同时说话会混成一句，目前不区分说话人。
- 半句字幕会随着对方继续说而改写，偶尔会跳动。
- 字幕不做任何过滤：队友骂人、说脏话或聊敏感话题，都会照原意翻译出来（脏话的语气可能比原话略轻）。
- 游戏术语的识别主要靠事后纠正，生僻的说法还是可能识别错；遇到了请把「识别成了什么 / 实际是什么」提给我们，加进词表就能修好。

### 字幕不对时：录一段发给我们

识别错、翻译错、几句话粘在一起时，最有用的反馈是一段录音。在托盘菜单勾选「录音用于反馈问题」，让问题重现一会儿，再点一次停止（最长 10 分钟，到时自动停）。程序会在 `recordings` 文件夹里存两个同名文件并打开这个文件夹：

- `.wav`：程序听到的声音，和交给识别模型的一模一样
- `.txt`：当时显示过的每一条字幕（识别出的原文、译文、时间）

录音只保存在你的电脑上，不会上传。里面有别人的声音，要不要发给别人由你决定。命令行里也可以用 `uv run valoroyce run --record`。

## 致谢与许可

代码以 [MIT](LICENSE) 许可发布。运行时下载的组件各有自己的许可：
[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)（Apache-2.0）、
[SenseVoice](https://github.com/FunAudioLLM/SenseVoice)（模型许可见其仓库）、
[Silero VAD](https://github.com/snakers4/silero-vad)（MIT）、
[Hy-MT2](https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF)（Apache-2.0）、
[llama.cpp](https://github.com/ggml-org/llama.cpp)（MIT）。

本项目与 Riot Games 无关。VALORANT 及相关名称是 Riot Games 的商标。
