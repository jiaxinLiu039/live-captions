# 🎙 实时英中字幕 · Live Captions

一个把**英语语音实时识别并翻译成中文字幕**的小工具。听英文讲座、会议、网课时,浏览器里实时显示中英双语字幕。

> ⚠️ **定位说明**:这是一个**自用/学习向**的项目,不是生产级服务。默认无鉴权、无高可用、无横向扩展。目前**仅支持英→中**单一方向。

## 核心流程

```
浏览器麦克风 (16kHz PCM)
  → WebSocket
  → 阿里云 Paraformer 实时识别英文
  → 长句按标点优先切分，核对识别回改
  → 携带最近成功的双语上下文，调用 DeepSeek 流式翻译
  → 按每行请求版本过滤过期结果
  → WebSocket 推回浏览器渲染
```

## 能做什么 / 不能做什么

**确认可用:**
- 麦克风采集 + 实时英→中双语字幕
- 边识别边"渐进式"提前翻译,降低等待感
- 长句优先在完整句／分句标点处切分，长度与等待时间兜底
- 同一行翻译带版本号，旧请求不能覆盖新译文或最终译文
- 翻译携带最近成功的英文原文＋中文译文，保留现有术语表
- 前端填写、保存和切换课程背景、本节主题及课程术语，配置随项目持久保存
- 停止录音后等待最后译文；失败和超时单独提示，不作为译文正文
- 暗色模式、音频波形、时间戳、复制到剪贴板、导出双语 .txt
- 系统音频捕获(屏幕共享)与麦克风+系统混合输入
- Document Picture-in-Picture 浮窗字幕
- 断线指数退避自动重连
- 基于转录内容的 AI 聊天页(总结/纪要/答疑)
- 通过 `.env` 或前端设置面板调整部分参数

**如实说明的限制:**
- **语言方向固定**:只支持英→中。协议里有 `detected_language` 字段,但**没有做语言检测**,中文/其他语种不支持。
- **鉴权默认关闭**:`AUTH_TOKEN` 为空时 `/ws` 不校验。生产/公网使用前**必须**自行设置。
- **费用估算偏低**:UI 里的预估只统计定稿翻译,未计入渐进式翻译的额外调用,实际花费会高于显示值。
- **服务端记录有上限**:单会话超过 2000 行会丢弃最早的记录(前端已渲染的不受影响,但依赖服务端导出时注意)。
- **并发翻译无上限**:语速很快时可能瞬间产生较多对 DeepSeek 的并发请求。
- **断句是规则启发式**:并非完整的语义理解；达到等待／长度上限时仍可能切开残句。字幕稳定或测试通过不代表识别／翻译准确度已经提高，实际效果需用课堂音频对比。
- **上下文有成本**:历史会增加输入 token。前一段译文未完成时只使用已有的成功历史，不额外阻塞当前翻译；错误和草稿不进入上下文。
- **断线无法补回音频**:重连会开启新的服务端会话和上下文，未收到的音频及译文不会自动恢复。

### 课程翻译配置

主页录音区可直接选择「课堂课程」，旁边的编辑按钮打开课程配置；右上角「设置」中也可填写课程名称、课程背景、本节主题和课程术语表，点击「保存并使用」。可以保存多门课程并通过下拉框切换；「通用课程」关闭课程背景与热词，「新建课程」创建另一份配置。「重新加载」读取项目中最新的配置。听力练习页顶部也有「课程配置」入口，共用同一份数据。主界面与配置表单的课程选择保持同步，切换前会提示尚未保存的编辑。

顶部保留搜索、导出、浮窗及设置，复制、听力练习、统计、清空集中在「更多」菜单。菜单支持方向键和 Escape。双语字幕采用居中的阅读宽度与更清楚的字号层级，主页及听力页共用配色和线性图标。

课程配置保存到与 `server.py` 同目录的 `course_profiles.json`，包含课程列表及最后选中的课程。重启项目、刷新页面或换浏览器后都会从服务端加载，不依赖浏览器本地存储。备份或迁移时复制此文件即可；文件默认被 Git 忽略，避免提交个人课程资料。

项目内置「最优化理论」「量子计算与AI」「深度学习」三门课程的通用背景和基础术语，来源文件为 `course_presets.json`。新项目没有个人配置文件时自动加载这些预设；之后的编辑和删除保存在个人配置文件中，不会被预设覆盖。本节主题留空，按实际课堂内容填写。

保存或切换在当前连接的后续翻译请求中生效，已有字幕不会自动重译，正在进行的请求保留开始时的配置。通用术语表仍可同时使用；课程术语在符合原文语境时优先。课程资料用于理解词义，不能保证修复识别错误或提高真实翻译准确度。配置文件损坏或写入失败会显示错误，并保留原文件。

### 英语识别与课程热词

识别器使用 `paraformer-realtime-v2`，启动时明确传入 `language_hints=["en"]`。在前端选择课程后再开始录音或听力识别，服务端从已保存的课程术语表提取英文词条，创建或更新阿里云热词表，并通过 `vocabulary_id` 传给识别器。格式如 `Hessian = Hessian 矩阵`：左侧用于识别热词，完整映射用于翻译。课程背景和本节主题用于翻译，不作为识别热词上传。

英文词条去重后最多 500 个，每个最多 7 个单词，默认权重 4、语言 `en`；纯中文、数字和不支持的词条会跳过。云端词表按课程、模型、账号及接口地址复用，缓存保存为项目中的 `asr_vocabulary_cache.json`（Git 忽略，不保存 API Key），减少重复创建。阿里云建议同一词表更新间隔至少 5 分钟，避免短时间反复修改并启动；接口规则见[热词文档](https://help.aliyun.com/zh/model-studio/improve-asr-accuracy)。

前端会等待热词准备和识别启动完成，再发送音频；听力视频也会等待后再播放。课程配置区显示本次识别语言、课程和实际加载的热词数。热词加载失败或超时时，会显示提示并使用英语通用识别。录音过程中编辑或切换课程，翻译随后的请求使用新配置，识别热词在下一次开始或重新连接时生效。「通用课程」不加载课程热词；独立的通用术语表仍仅用于翻译。

### 翻译更新、上下文与断句

每行的阶段性／最终翻译共用递增的 `version`。发起新请求时尝试取消旧任务，服务端在发送和保存时核对版本，两个前端也忽略旧版本。最终翻译开始后，此前的草稿请求持续失效。新请求等待首个 token 时保留上一版文字，但取消最终确认状态。

双语上下文默认取当前段之前、按原文出现顺序排列的最近 3 段成功定稿，包含英文和中文。并发完成顺序、长句生成的特殊行号不会用于决定上下文顺序。请求只翻译当前英文，不重复历史；同词数的识别回改也可以触发新一版阶段性翻译。失败、空结果、提前中断的翻译流不会成为成功上下文。

`MAX_SENTENCE_WORDS` 现在是**切分目标词数**。接近目标时，优先选择连续识别中已稳定的句号、问号、分号，其次选择两侧有足够文字的逗号；避开常见缩写和小数。找不到边界时，最多允许额外 `SENTENCE_SPLIT_GRACE_WORDS` 个词，或等待 `SENTENCE_SPLIT_WAIT` 秒再兜底切分。等待使用独立定时器，新识别回调不会无限延后截止时间；`0` 关闭提前切分，阶段性翻译仍可运行。

识别服务返回累计原文时，通过文本范围映射处理前文增词、删词和替换。已切分范围若被回改，会更新原文并重新翻译受影响的行；删除的范围清空对应文字。最终原文的各段保持连续、不重复、不遗漏。切分出的行目前沿用源句开始时间，不提供精确词级时间戳。

点击停止后先结束采集并发送空音频帧，连接继续接收字幕。服务端最多等待 5 秒停止 ASR，再等待翻译任务完成（默认最多 10 秒），发送 `ready_to_stop`。主页收到确认后再保存记录；两个前端均有连接中断／超时兜底，并按服务端会话隔离行号，避免重连后覆盖旧行。超时或失败的记录保留未完成状态。

## 快速开始

### 依赖
- Python 3.11+
- 阿里云 Dashscope API Key(实时 ASR)
- DeepSeek API Key(翻译)

### 步骤
```bash
cd translater
pip install -r requirements.txt

# 创建 .env(见下),切勿提交到 git
python server.py
```
浏览器打开 **http://localhost:8000**,点「开始录音」。

### `.env` 配置项

| 变量 | 说明 | 默认 |
|------|------|------|
| `DASHSCOPE_API_KEY` | 阿里云语音识别 Key | 必填 |
| `DEEPSEEK_API_KEY` | DeepSeek 翻译 Key | 必填 |
| `AUTH_TOKEN` | WebSocket 认证令牌 | 空(不启用) |
| `ASR_HOTWORD_WEIGHT` | 英语课程热词权重（1–5） | 4 |
| `ASR_HOTWORD_TIMEOUT` | 课程热词加载等待上限（秒） | 15 |
| `MAX_SENTENCE_WORDS` | 长句切分目标词数，优先标点(`0`=关闭提前切分) | 25 |
| `SENTENCE_SPLIT_GRACE_WORDS` | 无合适边界时允许超出目标的词数 | 10 |
| `SENTENCE_SPLIT_WAIT` | 达到目标词数后的最大切分等待时间(秒) | 3 |
| `TRANSLATION_CONTEXT_WINDOW` | 最近成功双语上下文的段数(`0`=关闭) | 3 |
| `TRANSLATION_CONTEXT_CHARS` | 上下文英文＋中文总字符预算(`0`=关闭) | 3000 |
| `TRANSLATION_DRAIN_TIMEOUT` | 停止 ASR 后等待最后译文的上限(秒) | 10 |
| `PROGRESSIVE_THRESHOLD` | 提前翻译触发词数 | 12 |
| `PROGRESSIVE_INTERVAL` | 提前翻译最小间隔(秒) | 2 |
| `PROGRESSIVE_EXTRA_WORDS` | 提前翻译需新增词数 | 4 |
| `ASR_COST_PER_SEC` | 语音识别单价(元/秒) | 0.00024 |
| `TRANSLATE_COST_PER_SENTENCE` | 翻译单价(元/句) | 0.00002 |

其余固定参数(监听地址、端口、最大连接数等)见 [config.py](config.py)。默认 `host=0.0.0.0`、`port=8000`、`max_connections=10`。

主页的切分／提前翻译设置会在连接后发送给服务端，覆盖该连接对应的默认值；其余新增参数通过 `.env` 配置，修改后需重启服务。

## 项目结构

```
translater/
├── server.py              # 主入口:FastAPI + /ws + 热词/会话/聊天/听力接口
├── config.py              # 配置加载(.env)
├── asr_bridge.py          # Paraformer 线程回调 → 异步 WebSocket,长句切分 + 渐进翻译
├── segmenter.py           # 英文边界规则 + 累计识别文本范围映射
├── session.py             # 单连接会话状态(线程安全)
├── protocol.py            # WebSocket 消息协议(V1 兼容 + V2)
├── deepseek_translate.py  # DeepSeek 流式翻译,双语历史 + 术语表,错误单独抛出
├── course_profile.py      # 课程资料校验 + 项目配置文件持久化
├── course_profiles.json   # 保存的课程及选择（首次保存时创建，Git 忽略）
├── course_presets.json    # 三门课程的基础背景与术语
├── asr_vocabulary.py      # 提取英文课程热词 + 云端词表复用
├── asr_vocabulary_cache.json # 云端词表 ID 缓存（自动创建，Git 忽略）
├── tests/                 # Python 模拟流/连接测试 + Node 前端状态测试
├── routes/                # /health、/v1/models
├── static/                # 前端:index.html / chat.html / listen.html / audio-worker.js
│                          #   (原生 HTML/CSS/JS,无框架、无构建)
│                          #   translation-state.js 共用版本过滤与停止握手
├── hotwords/              # 术语表 .txt(已被 gitignore)
└── downloads/             # 会话导出 JSON(已被 gitignore)
```

## API

**WebSocket 实时转写**
```
WS /ws?mode=full&token=xxx
```
- 客户端 → 服务端:二进制 PCM16 音频帧(16kHz mono);空帧 = 结束信号
- 服务端 → 客户端:JSON(`config` / `result` / `translation_stream` / `translation_error` / `error` / `stats` / `ready_to_stop`)

`config.stop_timeout_ms` 表示客户端停止握手的等待上限，包含 ASR 停止、翻译收尾和网络余量。`translation_stream` 示例：

```json
{"type":"translation_stream","line_id":12,"version":3,"accumulated":"这个模型利用上下文减少错误。","final":true,"state":"complete"}
```

`state` 为 `pending`、`streaming` 或 `complete`；`final=true` 才表示最终译文成功确认。`result.version` 让前端在原文回改时取消旧的最终确认。失败单独发送 `translation_error`，包含 `line_id`、`version` 和 `msg`，不能将其作为中文正文。`ready_to_stop.complete=false` 表示停止时仍有识别／翻译未完成。

### 本地验证（不调用付费 API）

在包含 `server.py` 的目录运行：

```bash
python -m unittest discover -s tests -v
node tests/test_translation_state.js
node tests/test_course_profiles.js
node tests/test_listen_startup.js
```

Python 测试使用模拟识别和翻译流，覆盖乱序返回、最终版本保护、双语上下文顺序／预算、断句与识别增删词、错误、超时和真实本地 WebSocket 停止握手；Node 测试覆盖前端版本过滤、连接隔离、停止确认及页面脚本语法。Python WebSocket 测试额外需要 `httpx`（`pip install httpx`）；Node 仅用于测试，不是运行服务的依赖。这些测试验证流程正确性，不评估真实模型准确度。

课程配置测试还覆盖项目文件的保存、切换、重载、删除、损坏文件保护和写入失败保护，以及课程资料通过 WebSocket 进入翻译提示词的流程。

**REST**

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/v1/models` | 模型列表(OpenAI 兼容) |

> `/v1/models` 不受 `AUTH_TOKEN` 保护,仅返回模型名,无副作用。

## 技术栈

- **后端**: Python 3.11+ / FastAPI / uvicorn / aiohttp
- **ASR**: 阿里云 Dashscope `paraformer-realtime-v2`
- **翻译**: DeepSeek Chat API(流式 SSE)
- **前端**: 原生 HTML/CSS/JS,Web Audio API + AudioWorklet

## 安全注意(重要)

1. **`.env` 含真实密钥**,已在 `.gitignore` 中排除——提交前请 `git status` 确认它未被暂存。**若曾把带密钥的仓库设为 public,请立即到对应平台后台轮换密钥。**
2. 公网/局域网使用前请设置 `AUTH_TOKEN`,并用 nginx 反代 + HTTPS(麦克风 API 需安全上下文,局域网访问要 HTTPS)。
3. 建议将 `host` 改为 `127.0.0.1`,不要直接把服务暴露到公网。
4. 无鉴权、无限流的情况下,任何能访问端口的人都可消耗你的 API 额度。

## License

MIT
