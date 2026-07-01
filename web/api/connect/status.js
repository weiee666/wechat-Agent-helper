// Vercel serverless：代理到腾讯后端的 /connect/status?ticket=...
export default async function handler(req, res) {
  const base = (process.env.TENCENT_API_BASE || '').trim();
  if (!base) return res.status(500).json({ error: 'TENCENT_API_BASE not set' });
  const ticket = req.query.ticket || '';
  try {
    const r = await fetch(`${base}/connect/status?ticket=${encodeURIComponent(ticket)}`);
    const data = await r.json();
    res.status(r.status).json(data);
  } catch (e) {
    res.status(502).json({ status: 'error', error: String(e) });
  }
}
