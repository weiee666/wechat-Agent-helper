# weixin-agent 看板（React 版）

飞书美学（Semi Design）+ Vite + React 18 版本，替代 `web/dashboard.html`。

## 本地跑

```bash
cd web/dashboard-react
npm install
npm run dev
# 打开 http://localhost:5173/?user_id=xxx&token=xxx
```

（本地 dev 需要 vercel-cli 起 serverless 代理，或者手写 proxy 到腾讯云。见 `vite.config.js` 的 `server.proxy`。）

## 部署到 Vercel

**推荐方式**：在 Vercel dashboard 建一个新项目，配置：

- **Root Directory**: `web/dashboard-react`
- **Framework Preset**: Vite（自动识别）
- **Build Command**: `npm run build`（默认）
- **Output Directory**: `dist`（默认）
- **Environment Variables**:
  - `TENCENT_API_BASE`: `http://43.142.81.135:7997`（beta）或 `:8080`（prod）

推完后新看板的 URL 是 Vercel 分配的 domain。老看板 `web/dashboard.html` 继续可用，不影响。

## 目录结构

```
web/dashboard-react/
├── api/dashboard/       # Vercel serverless functions（跟老看板同套）
├── src/
│   ├── App.jsx          # 主组件（状态 + 装配）
│   ├── main.jsx
│   ├── api.js           # fetch 封装
│   ├── pusher.js        # Pusher 订阅
│   ├── state.js         # 会话状态 + participantsOf
│   ├── styles.css
│   ├── avatars/         # 4 个 SVG 头像组件
│   │   ├── UserAvatar.jsx
│   │   ├── BotAvatar.jsx
│   │   ├── TeacherAvatar.jsx
│   │   ├── ClaudeAvatar.jsx
│   │   └── registry.jsx
│   └── components/
│       ├── Sidebar.jsx           # 左侧会话列表（Semi Nav）
│       ├── MessageList.jsx       # 消息流 + 气泡渲染
│       ├── Composer.jsx          # 输入框（Semi TextArea + Button）
│       └── ParticipantsGroup.jsx # 右上角 Semi AvatarGroup（重叠头像）
├── index.html
├── package.json
├── vite.config.js
├── vercel.json
└── README.md
```
