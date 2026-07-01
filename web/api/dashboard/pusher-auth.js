// Vercel serverless：Pusher private channel 订阅签名代理。
// 浏览器 subscribe private-* 前会 POST 到此端点，body 是 form-encoded：socket_id + channel_name
// 我们透传 body 到后端 /pusher/auth?token=xxx，后端签名后返回 {auth: "key:sig"}
export const config = { api: { bodyParser: false } };

export default async function handler(req, res) {
  const base = (process.env.TENCENT_API_BASE || '').trim();
  if (!base) return res.status(500).json({ error: 'TENCENT_API_BASE not set' });
  if (req.method !== 'POST') return res.status(405).json({ error: 'method not allowed' });
  const token = req.query.token || '';
  if (!token) return res.status(400).json({ error: 'token 必填' });

  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const body = Buffer.concat(chunks);

  try {
    const r = await fetch(`${base}/pusher/auth?token=${encodeURIComponent(token)}`, {
      method: 'POST',
      headers: {
        'content-type': req.headers['content-type'] || 'application/x-www-form-urlencoded',
      },
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
