// Vercel serverless：看板通用聊天代理。
// POST /api/dashboard/panel-chat  body: {user_id, token, conv_id, message}
export const config = { api: { bodyParser: false } };

export default async function handler(req, res) {
  const base = (process.env.TENCENT_API_BASE || '').trim();
  if (!base) return res.status(500).json({ error: 'TENCENT_API_BASE not set' });
  if (req.method !== 'POST') return res.status(405).json({ error: 'method not allowed' });

  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const body = Buffer.concat(chunks);

  try {
    const r = await fetch(`${base}/panel/chat`, {
      method: 'POST',
      headers: { 'content-type': req.headers['content-type'] || 'application/json' },
      body,
    });
    const text = await r.text();
    res.status(r.status);
    const ct = r.headers.get('content-type');
    if (ct) res.setHeader('content-type', ct);
    return res.send(text);
  } catch (e) {
    return res.status(502).json({ error: `proxy failed: ${String(e)}` });
  }
}
