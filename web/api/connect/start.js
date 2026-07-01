// Vercel serverless：代理到腾讯后端的 /connect/start。
// 前端只连 Vercel(https)，这里在服务器侧去连腾讯 http —— 绕开浏览器混合内容限制。
// 在 Vercel 项目环境变量里设 TENCENT_API_BASE，如 http://43.142.81.135:8080
export default async function handler(req, res) {
  const base = process.env.TENCENT_API_BASE;
  if (!base) return res.status(500).json({ error: 'TENCENT_API_BASE not set' });
  try {
    const r = await fetch(`${base}/connect/start`, { method: 'POST' });
    const data = await r.json();
    res.status(r.status).json(data);
  } catch (e) {
    res.status(502).json({ status: 'error', error: String(e) });
  }
}
