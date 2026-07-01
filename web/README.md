# web —— 自助开通门户（部署到 Vercel）

静态前端 + serverless 代理。前端只连 Vercel(https)，代理函数在服务器侧去连腾讯 http 后端，
绕开浏览器混合内容限制。每个访客打开页面 → 现生成一张**专属新二维码** → 扫码绑定自己的账号。

```
web/
├── index.html              静态页（二维码 + 状态轮询）
└── api/connect/
    ├── start.js            POST → 代理腾讯 /connect/start
    └── status.js           GET  → 代理腾讯 /connect/status
```

## 部署步骤

1. 腾讯服务器先把后端跑起来（监听 :8080），并在安全组放行 8080。
2. 在 Vercel 新建项目，**Root Directory 选 `web`**（或单独把 web/ 推成一个仓库）。
3. Vercel 项目 → Settings → Environment Variables 加：
   ```
   TENCENT_API_BASE = http://43.142.81.135:8080
   ```
4. Deploy。打开 Vercel 给的网址，应能看到二维码并可扫码绑定。

## 为什么必须有后端、不能纯静态

一个二维码 = 一次性登录令牌，只能绑一个账号。多个用户扫同一张码会互相抢、只有一人成功。
所以每个访客都得由后端**现生成一张新码**并单独轮询——这是动态的，纯静态站做不到。
