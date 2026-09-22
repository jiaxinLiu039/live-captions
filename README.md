# 🎙 实时英中字幕 · Live Captions

一个把**英语语音实时识别并翻译成中文字幕**的小工具。听英文讲座、会议、网课时,浏览器里实时显示中英双语字幕。

> ⚠️ **定位说明**:这是一个**自用/学习向**的项目,不是生产级服务。默认无鉴权、无高可用、无横向扩展。目前**仅支持英→中**单一方向。

## 核心流程

```
浏览器麦克风 (16kHz PCM)
  → WebSocket
  → 阿里云 Paraformer 实时识别英文
  → DeepSeek API 流式翻译成中文
  → WebSocket 推回浏览器渲染
```

## 能做什么 / 不能做什么

**确认可用:**
- 麦克风采集 + 实时英→中双语字幕
- 边识别边"渐进式"提前翻译,降低等待感
- 长句自动切块定稿(说话人长时间不停顿时的处理)
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
| `MAX_SENTENCE_WORDS` | 单句词数上限,超出切块(`0`=关闭) | 25 |
| `PROGRESSIVE_THRESHOLD` | 提前翻译触发词数 | 12 |
| `PROGRESSIVE_INTERVAL` | 提前翻译最小间隔(秒) | 2 |
| `PROGRESSIVE_EXTRA_WORDS` | 提前翻译需新增词数 | 4 |
| `ASR_COST_PER_SEC` | 语音识别单价(元/秒) | 0.00024 |
| `TRANSLATE_COST_PER_SENTENCE` | 翻译单价(元/句) | 0.00002 |

其余固定参数(监听地址、端口、最大连接数等)见 [config.py](config.py)。默认 `host=0.0.0.0`、`port=8000`、`max_connections=10`。

## 项目结构

```
translater/
├── server.py              # 主入口:FastAPI + /ws + 热词/会话/聊天/听力接口
├── config.py              # 配置加载(.env)
├── asr_bridge.py          # Paraformer 线程回调 → 异步 WebSocket,长句切分 + 渐进翻译
├── session.py             # 单连接会话状态(线程安全)
├── protocol.py            # WebSocket 消息协议(V1 兼容 + V2)
├── deepseek_translate.py  # DeepSeek 流式/非流式翻译,支持术语表
├── routes/                # /health、/v1/models
├── static/                # 前端:index.html / chat.html / listen.html / audio-worker.js
│                          #   (原生 HTML/CSS/JS,无框架、无构建)
├── hotwords/              # 术语表 .txt(已被 gitignore)
└── downloads/             # 会话导出 JSON(已被 gitignore)
```

## API

**WebSocket 实时转写**
```
WS /ws?mode=full&token=xxx
```
- 客户端 → 服务端:二进制 PCM16 音频帧(16kHz mono);空帧 = 结束信号
- 服务端 → 客户端:JSON(`config` / `result` / `translation_stream` / `stats` / `ready_to_stop`)

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
