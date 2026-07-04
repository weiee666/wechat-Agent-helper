# weixin-agent

微信里跑的多 Agent 系统。
1. 通过腾讯新开源的iLink协议，实现每个人在微信里有自己的 AI 助手，你和你的助手可以在微信里直接对话。
2. 通过A2A协议实现用户的助手之间可以互相通话。
3. 在平台上设计了公共的Agent供用户的AI助手调用，同时平台可以接入自己本机的Claude Code，参考 https://github.com/hao-ji-xing/cc-weixin
。
4. 通过跟助手发送 **“看板”**，能实时看到所有对话，包括 Agent 之间私下聊了啥。

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
- Semi Design
- DiceBear 出 SVG 头像（shapes / thumbs / initials 三种风格）
- Pusher-js 订阅私有 channel
- marked + DOMPurify 渲染 Markdown 顺便挡 XSS

**本地 Claude 集成**
- 反向 WebSocket：Mac 上的 daemon 主动连服务器
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
- 一个 iLink Bot 账号和一个微信号（如果想要测试Agent之间的对话功能，最好微信申请测试账号，然后手机端应用复制）
- DeepSeek API key
- Pusher 应用（要 `app_id / key / secret / cluster` 四样）-可选，如果有自己的服务器和域名则不需要
- Tavily API key（联网搜索tool需要）
- 想让助手调 Claude Code 的话，需要本地本地已安装Claude Code

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

**把前端部署到网上（两条路，二选一）**

**路 A：托管到 Vercel（我在用的，最省事）**

Vercel 免费额度够个人用，静态托管和 serverless functions 一起给了。步骤：

1. 在 Vercel 后台点 Add New Project，导入这个 GitHub 仓库
2. Configure 里 **Root Directory** 选 `web`（不要选仓库根，因为 Vite 项目在 `web/` 下）
3. Framework Preset 那栏 Vercel 会自动认成 Vite，Build Command 和 Output Directory 都用默认
4. Environment Variables 里加一个 `TENCENT_API_BASE`，值填 `http://<你的服务器公网 IP 或域名>:8080`
5. Deploy

代理层就是仓库里 `web/api/dashboard/*.js` 那几个文件，Vercel 会把它们识别成 serverless functions 自动部署。浏览器请求 `/api/dashboard/config` 会走这里再转到你后端。你只需要提供后端 URL，其他什么都不用改。

**路 B：自己搭（不想用 Vercel，或者想全套自己控）**

前端和代理都放你自己的服务器上，用 nginx 反代做代理层：

1. 本地 `cd web && npm run build`，生成 `web/dist/` 是纯静态文件
2. 把 `dist/` 传到服务器，比如 `/var/www/weixin-dashboard/`
3. nginx 配置，静态文件走托管，`/api/dashboard/*` 反代到 8080：

```nginx
server {
    listen 443 ssl;
    server_name dashboard.你的域名.com;
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    # SPA 静态资源
    root /var/www/weixin-dashboard;
    index index.html;
    location / {
        try_files $uri $uri/ /index.html;
    }

    # 代理层：把 /api/dashboard/* 转到后端
    location /api/dashboard/ {
        # 把 Vercel serverless functions 里的转发逻辑手动搬过来，
        # 或者简单点：直接反代整个 /panel/* 到后端
        proxy_pass http://127.0.0.1:8080/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

注意路径映射：Vercel serverless 版本里 `/api/dashboard/config` 会被转成 `/session/{uid}/config`（还带鉴权头），nginx 直接 `proxy_pass` 不会做这层转换。要么你在后端加一层路由别名，要么把 `web/api/dashboard/*.js` 里那点 Node.js 转发逻辑改写成 nginx `rewrite` 规则。想省事就走 A。

最后不管走哪条路，都要把 `.env` 里的 `DASHBOARD_URL_BASE` 改成你实际用的 URL：Vercel 给的 `https://xxx.vercel.app/dashboard.html`，或者你自己域名 `https://dashboard.你的域名.com/dashboard.html`。

**Claude Code 桥接（可选）**

在 Mac 上：

```bash
cd weixin-agent
python3 scripts/install_claude_bridge.py
# 输入服务端签的 API Key，脚本自动装 launchd 服务
```

装完之后 Mac 后台常驻，服务器上的 Claude Agent 就自动 online 了。

**成功**

在微信里直接跟 bot 说话就行。想看背后到底发生了啥，跟 bot 说一句「看板」，它会回一个 URL，点进去是实时对话面板。

---

## 功能介绍

- **微信里的私人助手**：每个用户在微信里有自己的 Agent。可以日常聊天、记事情、查资料、给自己或助手起名字。Agent 带短期记忆（SQLite 里的滑动窗口，加 LLM 压缩超长历史）和长期记忆（用户口述过的事实、任务）。

- **Agent 之间可以互相通话**：你能让自己的助手去联系另一个人的助手。链路是「你 → 你的助手 → 对方的助手 → 对方本人」。你的助手先跟对方的助手把事情谈清楚。碰上要对方本人拍板的事（几点方便、家里有没有酒），对方的助手会调 `notify_my_user` 工具把问题推到对方微信。对方回复了，助手再把答案带回你的助手，你的助手综合成一句自然的话告诉你。

- **老师 Agent**（可定制化公共Agent）：跨用户共享的教学 Agent。背后是一个Antrophic提供的教学skill，为开发者自己在搞懂不懂的知识点使用，可以在微信里让AI助手去找**老师Agent**提问，也可以在看板中直接向**老师Agent**提问。每个用户跟老师是独立 session，历史互不干扰。

- **Claude Code Agent**：让微信里的助手真的能跑代码、读写文件、执行 bash 命令。走的是反向 WebSocket，连到你 Mac 上跑的 Claude Code CLI。说「找 Claude 帮我写个脚本」就行。session 走 `--resume` 延续，多轮任务接着上次的往下走。

- **实时看板**：左侧栏是所有会话：跟自己助手、跟老师、跟 Claude，每一对 Agent-Agent 对话也各自一个 tab。右上角圆形头像重叠展示当前 channel 里有谁。

- **扫码开通**：新用户加入走 `/connect` 页，用微信扫二维码就能开一个新 iLink bot 账号，热起收消息线程，不用运维介入。

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
