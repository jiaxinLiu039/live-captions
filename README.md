# 🎙 实时英中字幕 · Live Captions

实时英语语音识别 + 流式中文翻译的 Web 应用。听英文讲座、会议、网课时，屏幕上实时显示中文字幕。

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

## ✨ 功能特性

| 功能 | 说明 |
|------|------|
| 🗣 实时语音识别 | 阿里云 Paraformer 实时 ASR，低延迟 |
| 🔤 流式翻译 | DeepSeek API 逐字符流式翻译，边识别边翻译 |
| 🌙 暗色模式 | 亮色 / 暗色 / 跟随系统，自动记忆 |
| 📊 音频波形 | 实时显示麦克风输入波形 |
| 🖥 系统音频捕获 | 支持捕获桌面/应用声音（屏幕共享） |
| 🎙+🖥 混合模式 | 麦克风 + 系统音频同时录制 |
| 📌 浮窗字幕 (PiP) | Document Picture-in-Picture 悬浮字幕窗口 |
| 🔄 自动重连 | 断线后指数退避自动重连，不丢录音 |
| ⏱ 时间戳 | 每句显示相对时间 |
| 📋 导出/复制 | 一键复制中文、导出英中双语 .txt |
| 💰 费用估算 | 实时显示 ASR + 翻译预估费用 |
| ⚙️ 设置面板 | 麦克风选择、主题、WebSocket 地址、传输模式 |
| 🔒 Token 认证 | 可选的连接认证保护 |
| 📡 REST API | OpenAI 兼容的文件转写接口 |

## 🏗 架构

```
┌─────────────────────────────────────────────────────────┐
│  浏览器                                                  │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────┐ │
│  │ AudioWorklet │───▶│  WebSocket   │◀──▶│  UI 渲染   │ │
│  │ 16kHz PCM   │    │  二进制音频   │    │  字幕显示   │ │
│  └─────────────┘    └──────┬───────┘    └────────────┘ │
└─────────────────────────────┼───────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│  FastAPI 服务器                                          │
│  ┌──────────────┐    ┌──────────────┐                   │
│  │ Aliyun ASR   │───▶│ DeepSeek API │                   │
│  │ Paraformer   │    │ 流式翻译      │                   │
│  │ (实时识别)    │    │ (EN→ZH)      │                   │
│  └──────────────┘    └──────────────┘                   │
└─────────────────────────────────────────────────────────┘
```

## 🚀 快速开始

### 1. 环境要求

- Python 3.11+
- 阿里云 Dashscope API Key（[获取](https://dashscope.console.aliyun.com/)）
- DeepSeek API Key（[获取](https://platform.deepseek.com/)）

### 2. 安装依赖

```bash
cd translater
pip install -r requirements.txt
```

### 3. 配置 API Key

创建 `.env` 文件：

```env
DASHSCOPE_API_KEY=你的阿里云key
DEEPSEEK_API_KEY=你的DeepSeek key
AUTH_TOKEN=          # 可选：设置后需要认证才能连接
```

### 4. 启动服务

```bash
python3 server.py
```

浏览器打开 **http://localhost:8000**，点击「开始录音」即可。

## 📁 项目结构

```
translater/
├── server.py              # 主入口：FastAPI + WebSocket + 路由
├── config.py              # 配置管理（.env 加载、默认值）
├── asr_bridge.py          # ASR 桥接（Paraformer 线程回调 → async）
├── session.py             # 会话状态管理（线程安全）
├── protocol.py            # WebSocket 消息协议定义
├── deepseek_translate.py  # DeepSeek 流式翻译
├── routes/
│   ├── health.py          # GET /health
│   ├── models.py          # GET /v1/models
│   └── transcriptions.py  # POST /v1/audio/transcriptions
├── static/
│   ├── index.html         # 前端单页应用（无构建系统）
│   └── audio-worker.js    # AudioWorklet 音频采集
├── .env                   # API 密钥（不要提交到 git！）
├── .gitignore
└── requirements.txt
```

## 🔌 API 接口

### WebSocket 实时转写

```
WS /ws?mode=full&token=xxx
```

- 客户端发送：二进制 PCM16 音频帧（16kHz mono）
- 服务端返回：JSON 消息（config / result / translation_stream / stats / ready_to_stop）

### REST API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/v1/models` | 模型列表（OpenAI 兼容） |
| POST | `/v1/audio/transcriptions` | 上传音频文件转写+翻译 |

#### 文件转写示例

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F file=@lecture.wav \
  -F response_format=json \
  -F translate_to_zh=true
```

支持的 `response_format`：`json` / `verbose_json` / `text` / `srt` / `vtt`

## ⚙️ 配置说明

在 `.env` 中可配置：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DASHSCOPE_API_KEY` | 阿里云语音识别 Key | 必填 |
| `DEEPSEEK_API_KEY` | DeepSeek 翻译 Key | 必填 |
| `AUTH_TOKEN` | WebSocket 认证令牌 | 空（不启用） |
| `MAX_SENTENCE_WORDS` | 单句词数上限（超出自动切分为多句，`0`=关闭） | `25` |
| `PROGRESSIVE_THRESHOLD` | 提前翻译触发词数 | `12` |
| `PROGRESSIVE_INTERVAL` | 提前翻译最小间隔（秒） | `2` |
| `PROGRESSIVE_EXTRA_WORDS` | 提前翻译需新增词数 | `4` |
| `ASR_COST_PER_SEC` | 语音识别单价（元/秒） | `0.00024` |
| `TRANSLATE_COST_PER_SENTENCE` | 翻译单价（元/句） | `0.00002` |

在 `config.py` 中可调整：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `host` | 监听地址 | `0.0.0.0` |
| `port` | 监听端口 | `8000` |
| `max_connections` | 最大并发连接数 | `10` |
| `max_upload_mb` | 文件上传大小限制 | `10` MB |
| `translation_context_window` | 翻译上下文句数 | `3` |
| `stats_interval` | 统计推送间隔 | `5` 秒 |
| `max_sentence_words` | 单句词数上限（超出自动切分定稿） | `25` |
| `progressive_threshold` | 提前翻译触发词数 | `12` |
| `progressive_interval` | 提前翻译最小间隔（秒） | `2` |
| `progressive_extra_words` | 提前翻译需新增词数 | `4` |
| `asr_cost_per_sec` | 语音识别单价（元/秒） | `0.00024` |
| `translate_cost_per_sentence` | 翻译单价（元/句） | `0.00002` |

> 💡 **长句截断 & 翻译频率**：说话人不停顿时，实时识别会把整句一直累积。超过 `max_sentence_words` 后，服务端自动把句子切分成定长块，每块独立翻译定稿，后续词继续开新行——避免单行无限变长和重复翻译。这些参数可在页面「设置」面板中实时调整，无需改代码。

## 🖥 局域网使用（另一台电脑访问）

麦克风 API 要求安全上下文。局域网访问需要 HTTPS：

```bash
# 生成自签名证书
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem \
  -days 365 -nodes -subj "/CN=你的IP"

# 修改 server.py 启动参数，或用 nginx 反向代理
```

然后用 `https://你的IP:8000` 访问（浏览器提示不安全时点「继续」）。

## 🎧 音频来源说明

| 模式 | 说明 | 场景 |
|------|------|------|
| 🎙 麦克风 | 传统麦克风输入 | 面对面听讲座 |
| 🖥 系统音频 | 通过屏幕共享捕获桌面声音 | 看网课、听在线会议 |
| 🎙+🖥 混合 | 两者混合 | 线上会议中也想录自己说的 |

> ⚠️ 选择「系统音频」时，浏览器会弹出屏幕共享弹窗，务必勾选 **「分享系统音频」**。

## 💰 费用估算

| 服务 | 定价 | 1小时讲座预估 |
|------|------|-------------|
| 阿里云 Paraformer | ~¥0.86/小时（¥0.00024/秒） | ¥0.86 |
| DeepSeek 翻译 | ~¥0.02/千句 | ¥0.01 |
| **合计** | | **~¥0.87** |

## 🛡 安全建议

1. **不要**把 `.env` 提交到 Git（已在 `.gitignore` 中排除）
2. 生产环境建议设置 `AUTH_TOKEN`
3. 使用 nginx 反向代理 + HTTPS
4. 不要将服务直接暴露到公网

## 📝 开发说明

### WebSocket 协议（V2）

```jsonc
// 服务端 → 客户端
{"type": "config", "version": "2.0", "session_id": "uuid", "mode": "full", ...}
{"type": "result", "sentence_id": 1, "en": "Hello", "en_final": true}
{"type": "translation_stream", "line_id": 1, "accumulated": "你好", "final": false}
{"type": "translation_stream", "line_id": 1, "accumulated": "你好世界", "final": true}
{"type": "stats", "audio_seconds": 60, "translate_count": 12, ...}
{"type": "ready_to_stop", "total_lines": 47}

// 客户端 → 服务端
[二进制帧]  PCM16 音频
[空帧]      结束信号
```

### 技术栈

- **后端**: Python 3.11+ / FastAPI / uvicorn / aiohttp
- **ASR**: 阿里云 Dashscope Paraformer-realtime-v2
- **翻译**: DeepSeek Chat API (流式 SSE)
- **前端**: 原生 HTML/CSS/JS，无框架无构建
- **音频**: Web Audio API + AudioWorklet

## 📄 License

MIT
