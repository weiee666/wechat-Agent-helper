// 看板 → Vercel serverless proxy → 腾讯云 FastAPI
const API = '/api/dashboard'

function url(path, params) {
  const q = new URLSearchParams(params).toString()
  return `${API}${path}${q ? '?' + q : ''}`
}

export async function getConfig(uid, token) {
  const r = await fetch(url('/config', { uid, token }))
  if (!r.ok) throw new Error((await r.json()).error || 'config 拉取失败')
  return r.json()
}

export async function getConversations(uid, token) {
  const r = await fetch(url('/panel-conversations', { uid, token }))
  if (!r.ok) return { conversations: [] }
  return r.json()
}

export async function getHistory(uid, token, conv_id, limit = 50) {
  const r = await fetch(url('/panel-history', { uid, token, conv_id, limit }))
  if (!r.ok) return { messages: [] }
  return r.json()
}

export async function sendMessage(uid, token, conv_id, message) {
  const r = await fetch(url('/panel-chat'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: uid, token, conv_id, message }),
  })
  if (!r.ok) throw new Error((await r.json()).error || '发送失败')
  return r.json()
}
