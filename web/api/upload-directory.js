// Vercel serverless：把浏览器上传的通讯录文件原始字节，转发到腾讯后端 /directory/upload。
// 前端只连 Vercel(https)，这里在服务器侧连腾讯 http 并附上同步令牌（令牌只在服务端，浏览器看不到）。
// Vercel 项目环境变量需配：TENCENT_API_BASE、DIRECTORY_SYNC_TOKEN
export const config = { api: { bodyParser: false } };

export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ error: 'method not allowed' });
  const base = (process.env.TENCENT_API_BASE || '').trim();
  const token = (process.env.DIRECTORY_SYNC_TOKEN || '').trim();
  if (!base || !token) return res.status(500).json({ error: 'TENCENT_API_BASE / DIRECTORY_SYNC_TOKEN 未配置' });

  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const body = Buffer.concat(chunks);

  try {
    const r = await fetch(`${base}/directory/upload`, {
      method: 'POST',
      headers: {
        'X-Sync-Token': token,
        'x-filename': req.headers['x-filename'] || '',
        'content-type': 'application/octet-stream',
      },
      body,
    });
    const text = await r.text();
    res.status(r.status).setHeader('content-type', 'application/json').send(text);
  } catch (e) {
    res.status(502).json({ error: String(e) });
  }
}
