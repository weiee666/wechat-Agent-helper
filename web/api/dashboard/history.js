// Vercel serverless：拉短期记忆历史。
// GET /api/dashboard/history?uid=xxx&token=yyy&limit=50
export default async function handler(req, res) {
  const base = (process.env.TENCENT_API_BASE || '').trim();
  if (!base) return res.status(500).json({ error: 'TENCENT_API_BASE not set' });
  const uid = req.query.uid || '';
  const token = req.query.token || '';
  const limit = req.query.limit || '50';
  if (!uid || !token) return res.status(400).json({ error: 'uid 和 token 必填' });
  try {
    const r = await fetch(`${base}/session/${encodeURIComponent(uid)}/history?token=${encodeURIComponent(token)}&limit=${encodeURIComponent(limit)}`);
    const text = await r.text();
    res.status(r.status);
    const ct = r.headers.get('content-type');
    if (ct) res.setHeader('content-type', ct);
    return res.send(text);
  } catch (e) {
    return res.status(502).json({ error: `proxy failed: ${String(e)}` });
  }
}
