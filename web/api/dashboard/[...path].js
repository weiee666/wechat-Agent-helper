// Vercel serverless catch-all：把 /api/dashboard/* 请求转发到腾讯后端。
// 浏览器 https 不能直连 http 后端；这层 serverless 做混合内容绕行。
//
// 需要环境变量 TENCENT_API_BASE，如 http://43.142.81.135:7997

export const config = { api: { bodyParser: false } };

export default async function handler(req, res) {
  const base = (process.env.TENCENT_API_BASE || '').trim();
  if (!base) return res.status(500).json({ error: 'TENCENT_API_BASE not set' });

  const parts = req.query.path || [];
  const subpath = Array.isArray(parts) ? parts.join('/') : String(parts);

  // 保留 query string（含 token）
  const idx = req.url.indexOf('?');
  const qs = idx >= 0 ? req.url.slice(idx) : '';
  const target = `${base}/${subpath}${qs}`;

  // 收集 body（POST form / json 都透传）
  let body;
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    body = Buffer.concat(chunks);
  }

  const headers = {};
  if (req.headers['content-type']) headers['content-type'] = req.headers['content-type'];

  try {
    const r = await fetch(target, {
      method: req.method,
      headers,
      body: body && body.length ? body : undefined,
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
