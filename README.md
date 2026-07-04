# weixin-agent

微信里跑的多 Agent 系统。每个人在微信里有自己的 AI 助手，助手之间可以互相通话、代传消息、找共享的老师 Agent，也可以调你 Mac 上的 Claude Code。配了一个飞书风格的看板，能实时看到所有对话，包括 Agent 之间私下聊了啥。

## 采用的技术

**后端**（Python 3.11+）
- FastAPI + Uvicorn 跑 HTTP / WebSocket
- LangChain + DeepSeek，走 tool loop
- SQLite（WAL 模式），存短期/长期记忆、pair 对话、bot 用户注册表、JWT 密钥
- iLink Bot API 接入微信，多账号并发收发
- Pusher（cluster=ap1）推看板实时事件，私有 channel
- Tavily Search 给 web_search 工具用
- Google A2A Protocol SDK 处理跨 Agent 通信
- HMAC-SHA256 JWT 做看板认证，7 天滑动续期

**前端**（React 18 + Vite）
- Semi Design（飞书官方开源组件库，配色也用飞书主蓝 `#3370FF`）
- DiceBear 出 SVG 头像（shapes / thumbs / initials 三种风格）
- Pusher-js 订阅私有 channel
- marked + DOMPurify 渲染 Markdown 顺便挡 XSS

**本地 Claude 集成**
- 反向 WebSocket：Mac 上的 daemon 主动连服务器（服务器在墙内，Mac 也没公网 IP，只能反着连）
- launchd 让 daemon 在 Mac 上后台常驻，开机自启，崩了自动拉起
- `claude --print --output-format json --resume <sid>` 保持每个用户的 Claude session

**部署**
- GitHub Actions：push main 或 beta 分支自动部到腾讯云
- systemd 上跑两个独立进程：prod 在 `:8080`，beta 在 `:7997`
- Vercel 部前端 SPA 加 serverless functions（代理到后端）

---

## 快速开始

**你需要准备**
- 能跑 Python 3.11+ 的机器（服务器或本地都行）
- 一个 iLink Bot 账号和一个微信号
- DeepSeek API key、Pusher 应用（要 `app_id / key / secret / cluster` 四样）
- 想联网搜索的话再来个 Tavily API key
- 想让助手调 Claude Code 的话，再准备一台装了 Claude Code 的 Mac

**跑起后端**

```bash
git clone https://github.com/weiee666/weixin-agent.git
cd weixin-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

项目根新建 `.env`：

```
DEEPSEEK_API_KEY=sk-xxx
PUSHER_APP_ID=xxx
PUSHER_KEY=xxx
PUSHER_SECRET=xxx
PUSHER_CLUSTER=ap1
TAVILY_API_KEY=tvly-xxx
DASHBOARD_URL_BASE=https://your-domain.vercel.app/dashboard.html
```

首次登一个 iLink 账号：

```bash
python -m app.main --login --qr-web
# 浏览器扫码，登完账号信息落到 data/accounts/
```

之后直接：

```bash
python -m app.main
# 拉起所有登过的账号，同时开通 API 监听 :8080
```

**跑起前端**

```bash
cd web
npm install
npm run dev
# 起在本地 :5173，需要 vercel-cli 或者手写个 proxy 把 /api 转到后端
```

部到 Vercel：Root Directory 选 `web`，环境变量 `TENCENT_API_BASE=http://<你的服务器>:8080`，剩下 Vite 自己认。

**Claude Code 桥接（可选）**

在 Mac 上：

```bash
cd weixin-agent
python3 scripts/install_claude_bridge.py
# 输入服务端签的 API Key，脚本自动装 launchd 服务
```

装完之后 Mac 后台常驻，服务器上的 Claude Agent 就自动 online 了。

**开始用**

在微信里直接跟 bot 说话就行。想看背后到底发生了啥，跟 bot 说一句「看板」，它会回一个 URL，点进去是实时对话面板。

---

## 功能介绍

**微信里的私人助手**：每个用户在微信里有自己的 Agent。可以日常聊天、记事情、查资料、给自己或助手起名字。Agent 带短期记忆（SQLite 里的滑动窗口，加 LLM 压缩超长历史）和长期记忆（用户口述过的事实、任务）。

**Agent 之间可以互相通话**：你能让自己的助手去联系另一个人的助手。链路是「你 → 你的助手 → 对方的助手 → 对方本人」。你的助手先跟对方的助手把事情谈清楚。碰上要对方本人拍板的事（几点方便、家里有没有酒），对方的助手会调 `notify_my_user` 工具把问题推到对方微信。对方回复了，助手再把答案带回你的助手，你的助手综合成一句自然的话告诉你。

**老师 Agent**：跨用户共享的教学 Agent，用苏格拉底追问加费曼类比讲解概念。想搞懂难题就说「问老师 XXX」，或者在看板老师 tab 里直接聊。每个用户跟老师是独立 session，历史互不干扰。

**Claude Code Agent**：让微信里的助手真的能跑代码、读写文件、执行 bash 命令。走的是反向 WebSocket，连到你 Mac 上跑的 Claude Code CLI。说「找 Claude 帮我写个脚本」就行。session 走 `--resume` 延续，多轮任务接着上次的往下走。

**实时看板**：飞书美学的 React SPA（Semi Design 组件，飞书主蓝 `#3370FF`）。左侧栏是所有会话：跟自己助手、跟老师、跟 Claude，每一对 Agent-Agent 对话也各自一个 tab。右上角圆形头像重叠展示当前 channel 里有谁。所有对话都落 SQLite 永久保留，刷新看板、换 token、服务器重启都不会丢。

**多用户互不冲突**：同一个浏览器同时开两个用户的看板 tab 各看各的，刷新不串号。URL 保留 user_id 用来定位 localStorage 里对应记录。token 走 JWT 无状态，每次访问自动续到当下加 7 天。

**双环境隔离**：main 分支自动部到 prod（`:8080`），beta 分支自动部到 beta（`:7997`）。systemd 两个 service 分开跑，数据库也分开。CI/CD 走 GitHub Actions，`git push` 之后大概一分钟自动上线。

**扫码开通**：新用户加入走 `/connect` 页，用微信扫二维码就能开一个新 iLink bot 账号，热起收消息线程，不用运维介入。

---

## 目录结构

```
weixin-agent/
├── app/                      # 后端 Python
│   ├── main.py               # 入口：iLink poller + FastAPI
│   ├── api.py                # 所有 HTTP / WebSocket 路由
│   ├── config.py             # env 读取 + 常量
│   ├── dashboard_tokens.py   # JWT 签发 / 验签
│   ├── agent/
│   │   ├── runner.py         # VoiceTaskAgent 主循环
│   │   ├── tools.py          # 所有 @tool（call_agent / notify_my_user 等）
│   │   ├── teacher.py        # 老师 Agent
│   │   ├── claude_agent.py   # Claude bridge hub
│   │   ├── memory_bridge.py  # 短期 + 长期记忆桥接
│   │   ├── llm.py            # DeepSeek 客户端
│   │   └── summarizer.py     # 内容整理
│   ├── channel/
│   │   ├── ilink.py          # iLink API 封装
│   │   ├── dispatch.py       # push_to_user（主动推消息）
│   │   └── stt.py            # 语音转文字
│   ├── core/memory/
│   │   ├── short_term.py     # SQLite stm_messages
│   │   ├── long_term.py      # SQLite ltm_items
│   │   ├── bot_users.py      # bot 用户注册表 + last_ctx
│   │   └── ...
│   ├── kernel/
│   │   └── bus.py            # 跨 Agent 授权流 / 系统通知
│   └── a2a/                  # A2A Protocol server
├── prompts/                  # 系统提示词
├── scripts/
│   ├── claude_bridge.py      # Mac 端 daemon
│   └── install_claude_bridge.py
├── web/                      # 前端 React
│   ├── src/
│   │   ├── App.jsx           # 主组件
│   │   ├── api.js            # fetch 封装
│   │   ├── pusher.js         # 订阅私有 channel
│   │   ├── state.js          # 会话状态
│   │   ├── avatars/          # 头像 registry
│   │   └── components/       # Sidebar / MessageList / Composer / ...
│   ├── api/dashboard/        # Vercel serverless proxy
│   ├── public/
│   │   ├── connect.html      # 扫码开通页
│   │   └── avatar-preview.html
│   └── vercel.json
└── data/                     # 运行态：SQLite / 账号 tokens（.gitignore）
```

---

## 一些设计上的取舍

- **A2A 对话双方看板都能看到**：Agent 之间对话推 pusher 到双方 channel，pair session 落 SQLite 双方共用一份。两边看板都能恢复出来。
- **助手代问的老师 / Claude 单独一个 pair tab**：老师和 Claude 有两种身份。用户直接聊走 `teacher:{uid}` / `claude:{uid}` session；助手代问走 `pair:{sorted uid|system:xxx}` session。看板里分成两个 tab，历史互不污染。
- **event loop 不阻塞**：panel_chat 里的 LLM 调用都走 `asyncio.to_thread` 丢到 executor thread，不占 uvicorn 主 loop。daemon WS 和其他 HTTP 请求不会因为 LLM 慢卡死。
- **Claude session 延续**：Mac daemon 记 per-user 的 Claude session_id，下次同一用户的请求带 `--resume`，多轮任务接着上次的往下走。

---

仓库：https://github.com/weiee666/weixin-agent
