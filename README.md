# Live Captions

实时英中字幕工具，适合听英文讲座、网课和会议。浏览器采集音频，阿里云 Paraformer 负责识别，DeepSeek 负责翻译，页面显示中英对照字幕。

目前只支持英语识别和中文翻译，主要用于本机运行。

## 运行

需要 Python 3.11+，以及阿里云 DashScope 和 DeepSeek 的 API Key。

在包含 `server.py` 的目录安装依赖：

```bash
python -m pip install -r requirements.txt
```

将 `.env.example` 复制为 `.env`，填入两个 Key：

```dotenv
DASHSCOPE_API_KEY=你的阿里云Key
DEEPSEEK_API_KEY=你的DeepSeekKey
```

启动服务：

```bash
python server.py
```

打开 [localhost:8000](http://localhost:8000)，允许浏览器使用麦克风后开始录音。

## 使用

主页可以选择课程、开始或暂停录音，并切换双语、中文、英文和字幕显示模式。音频来源在设置中选择，支持麦克风、系统音频和两者混合。系统音频通过屏幕共享获取，是否能采集取决于浏览器和共享对象。

停止录音后，页面会等最后几句翻译完成，再保存到左侧录音库。录音库保存在当前浏览器中，最多保留 20 条记录；换浏览器不会自动同步，可以用导出功能留存文本或字幕。

顶部提供搜索、导出、浮窗和设置。复制中文、听力练习、用量统计及清空在“更多”菜单中。可导出中文、英文、双语文本，以及 SRT、VTT 字幕。浮窗需要浏览器支持 Document Picture-in-Picture。

听力练习页可以导入本地视频或音频，调整播放速度、点击字幕跳转，也可以先隐藏中文，练习听懂后再查看。AI 助手可以根据当前转录内容回答问题或整理笔记。

## 课程配置

在主页选择“课堂课程”，旁边的编辑按钮用于修改课程名称、背景、本节主题和术语。也可以从设置中进入。填好后点击“保存并使用”，选择“通用课程”则不使用课程背景和热词。

内置三门课程：

- 最优化理论
- 量子计算与AI
- 深度学习

这些预设只有通用背景和基础词汇，本节主题需要按实际内容填写。术语表每行一组，例如：

```text
Hessian matrix = Hessian 矩阵
KKT conditions = KKT 条件
gradient descent = 梯度下降
```

翻译会参考课程背景、主题和中英术语。识别使用术语左侧的英文，通过阿里云热词接口加载；课程背景和主题不作为识别热词上传。识别语言固定为英语。

课程配置保存到项目中的 `course_profiles.json`，包括最后选中的课程。重启服务或换浏览器后仍可加载，备份时复制这个文件即可。首次使用时从 `course_presets.json` 读取预设，之后不会覆盖已有的个人配置。

录音中修改课程，后续翻译会使用新配置，已有字幕不会自动重译。识别热词需要下次开始或重新连接时才生效。独立的通用术语表只用于翻译。

热词去重后最多 500 个，每个最多 7 个英文单词，默认权重为 4。页面会显示是否加载成功及词条数量，加载失败时会提示，并继续使用英语通用识别。词表 ID 缓存在 `asr_vocabulary_cache.json`，同一课程会复用已有词表。云端配额和更新间隔见[阿里云热词文档](https://help.aliyun.com/zh/model-studio/improve-asr-accuracy)。

个人课程配置、词表缓存和 `.env` 都已加入 `.gitignore`。

## 参数

下面的参数可写入 `.env`，修改后重启服务。主页设置中的断句和提前翻译参数会覆盖当前连接的默认值。

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DASHSCOPE_API_KEY` | 阿里云语音识别 Key | 必填 |
| `DEEPSEEK_API_KEY` | DeepSeek 翻译 Key | 必填 |
| `AUTH_TOKEN` | WebSocket 连接令牌 | 空，不校验 |
| `ASR_HOTWORD_WEIGHT` | 课程热词权重，范围 1–5 | 4 |
| `ASR_HOTWORD_TIMEOUT` | 热词加载等待时间，秒 | 15 |
| `MAX_SENTENCE_WORDS` | 长句切分目标词数，0 为关闭提前切分 | 25 |
| `SENTENCE_SPLIT_GRACE_WORDS` | 没有合适标点时允许多出的词数 | 10 |
| `SENTENCE_SPLIT_WAIT` | 达到目标词数后最多再等多久，秒 | 3 |
| `TRANSLATION_CONTEXT_WINDOW` | 翻译参考的历史段数，0 为关闭 | 3 |
| `TRANSLATION_CONTEXT_CHARS` | 历史英文和中文的总字符上限，0 为关闭 | 3000 |
| `TRANSLATION_DRAIN_TIMEOUT` | 停止识别后等待翻译的时间，秒 | 10 |
| `PROGRESSIVE_THRESHOLD` | 触发提前翻译的词数 | 12 |
| `PROGRESSIVE_INTERVAL` | 提前翻译的最小间隔，秒 | 2 |
| `PROGRESSIVE_EXTRA_WORDS` | 再次提前翻译所需的新增词数 | 4 |
| `ASR_COST_PER_SEC` | 识别费用估算，元/秒 | 0.00024 |
| `TRANSLATE_COST_PER_SENTENCE` | 翻译费用估算，元/句 | 0.00002 |

监听地址、端口和连接数在 [config.py](config.py) 中设置，默认分别为 `0.0.0.0`、`8000` 和 `10`。

默认没有鉴权。`AUTH_TOKEN` 只保护 `/ws`，不保护课程配置等 REST 接口。本机使用可以将监听地址改为 `127.0.0.1`；需要让其他设备访问时，应另外配置访问控制和 HTTPS，避免别人修改配置或消耗 API 额度。设置连接令牌后，在页面的 WebSocket 地址中带上 `token` 参数。

## 已知限制

- 口音、噪声和专业术语仍可能识别出错，课程词表不能替代清楚的音频。
- 断句优先找标点，但超过长度或等待时间后仍可能切在半句话中。
- 断线会自动重连，但断线期间的音频无法补回，新连接也不会继承原来的翻译上下文。
- 费用统计没有计入所有提前翻译调用，实际账单可能高于页面显示值。
- 并发翻译请求没有单独设上限，连续快速讲话时可能产生较多请求。
- 服务端单个会话最多保留 2000 行，超过后丢弃最早的记录；前端已经显示的字幕不受影响。
- 时间戳按句段记录，不提供词级时间戳。

## 开发

后端使用 FastAPI、uvicorn 和 aiohttp，前端是原生 HTML、CSS、JavaScript，没有构建步骤。音频通过 Web Audio API 和 AudioWorklet 采集，以 16kHz 单声道 PCM16 发送到服务端。识别模型为 `paraformer-realtime-v2`，翻译模型为 `deepseek-chat`。

识别结果会随说话过程更新，翻译先显示草稿，再显示完整结果。每行带版本号，过期请求不会覆盖新内容。翻译默认参考前面三段已经完成的中英字幕；错误和未完成的草稿不进入上下文。

主要文件：

```text
server.py                  服务入口和接口
config.py                  配置
asr_bridge.py              识别回调与翻译调度
asr_vocabulary.py          课程热词加载和缓存
segmenter.py               断句及原文更新
session.py                 会话状态
protocol.py                WebSocket 消息格式
deepseek_translate.py      翻译请求
course_profile.py          课程配置读写
course_presets.json        课程预设
static/                    主页、听力页和聊天页
tests/                     Python 和前端测试
```

### WebSocket

```text
/ws?mode=full&token=xxx&course_id=课程ID
```

`token` 在设置 `AUTH_TOKEN` 后需要填写。省略 `course_id` 时使用项目中选中的课程，留空则使用通用课程。

客户端发送二进制音频帧，空帧表示结束录音。服务端返回 `config`、`result`、`translation_stream`、`translation_error`、`error`、`stats` 和 `ready_to_stop` 消息。

`config` 在识别器准备好后发送，客户端收到后再开始传音频。停止录音时，客户端等待 `ready_to_stop` 再关闭连接；等待上限由 `config.stop_timeout_ms` 指定。`complete=false` 表示仍有字幕未完成。

译文消息中的 `version` 用来过滤旧结果，`final=true` 表示当前版本的最终译文。翻译失败单独发送 `translation_error`，不会混入中文正文。具体字段见 [protocol.py](protocol.py)。

其他接口包括 `/health`、`/v1/models`、`/api/courses`，以及词表、会话和聊天接口，定义在 [server.py](server.py) 中。

### 测试

Python WebSocket 测试另需安装 `httpx`，前端测试需要 Node.js：

```bash
python -m pip install httpx
python -m unittest discover -s tests -v
node tests/test_translation_state.js
node tests/test_course_profiles.js
node tests/test_listen_startup.js
```

测试使用模拟识别和翻译服务，不调用付费 API。覆盖课程配置读写、热词加载与回退、字幕版本、断句、停止录音后的翻译处理，以及前端启动流程。实际识别和翻译效果需要用音频另行检查。Node.js 只用于测试，运行项目不需要安装。

## License

MIT
