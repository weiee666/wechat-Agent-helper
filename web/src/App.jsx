import { useEffect, useRef, useState, useCallback } from 'react'
import { Layout, Typography, Toast, Spin, Button } from '@douyinfe/semi-ui'
import { IconChevronLeft } from '@douyinfe/semi-icons'
import * as api from './api.js'
import { subscribeChannel } from './pusher.js'
import { defaultConversations, participantsOf } from './state.js'
import Sidebar from './components/Sidebar.jsx'
import MessageList from './components/MessageList.jsx'
import Composer from './components/Composer.jsx'
import ParticipantsGroup from './components/ParticipantsGroup.jsx'

const { Header, Sider, Content } = Layout
const { Title, Text } = Typography

// 多用户 auth：每个 user_id 独立 localStorage 记录，URL 保留 user_id 定位
//   - URL /dashboard.html?user_id=X&token=Y  → 首次登录，存 { X: Y }，URL 清 token 保留 user_id
//   - URL /dashboard.html?user_id=X         → 刷新，按 user_id 从 localStorage 取 token
//   - URL /dashboard.html                    → 无 user_id，回落到 lastUid
// 一次性迁移旧 key（老版本共享单个 auth）到新格式
const LS_PREFIX = 'weixin-dashboard-auth:'
const LS_LAST_UID = 'weixin-dashboard-last-uid'
const LS_OLD_KEY = 'weixin-dashboard-auth'

function migrateOldKey() {
  try {
    const raw = localStorage.getItem(LS_OLD_KEY)
    if (!raw) return
    const old = JSON.parse(raw)
    if (old && old.user_id && old.token) {
      localStorage.setItem(LS_PREFIX + old.user_id, JSON.stringify(old))
      localStorage.setItem(LS_LAST_UID, old.user_id)
    }
    localStorage.removeItem(LS_OLD_KEY)
  } catch {}
}
migrateOldKey()

function readAuth() {
  const p = new URLSearchParams(location.search)
  const uidParam = p.get('user_id')
  const tokenParam = p.get('token')

  // Case 1: URL 里带完整 auth（首次点微信链接过来）
  if (uidParam && tokenParam) {
    const auth = { user_id: uidParam, token: tokenParam }
    try {
      localStorage.setItem(LS_PREFIX + uidParam, JSON.stringify(auth))
      localStorage.setItem(LS_LAST_UID, uidParam)
    } catch {}
    // URL 里删掉 token 但保留 user_id：刷新时凭 user_id 定位 localStorage 记录
    try {
      const q = 'user_id=' + encodeURIComponent(uidParam)
      history.replaceState(null, '', location.pathname + '?' + q + location.hash)
    } catch {}
    return auth
  }

  // Case 2: URL 只有 user_id（刷新或书签）
  if (uidParam) {
    try {
      const raw = localStorage.getItem(LS_PREFIX + uidParam)
      if (raw) {
        try { localStorage.setItem(LS_LAST_UID, uidParam) } catch {}
        return JSON.parse(raw)
      }
    } catch {}
    return { user_id: uidParam, token: '' }
  }

  // Case 3: URL 空 → 回落到最近一次登录的用户
  try {
    const lastUid = localStorage.getItem(LS_LAST_UID)
    if (lastUid) {
      const raw = localStorage.getItem(LS_PREFIX + lastUid)
      if (raw) {
        // 把 user_id 补回 URL 让下次刷新明确
        try {
          const q = 'user_id=' + encodeURIComponent(lastUid)
          history.replaceState(null, '', location.pathname + '?' + q + location.hash)
        } catch {}
        return JSON.parse(raw)
      }
    }
  } catch {}
  return { user_id: '', token: '' }
}

function saveToken(uid, newToken) {
  if (!uid || !newToken) return
  try {
    localStorage.setItem(LS_PREFIX + uid, JSON.stringify({ user_id: uid, token: newToken }))
  } catch {}
}

function clearAuth(uid) {
  try {
    if (uid) localStorage.removeItem(LS_PREFIX + uid)
    // 不清 LS_LAST_UID —— 让用户可以尝试再打开旧链接
  } catch {}
}

const initial = readAuth()
let userId = initial.user_id
let token = initial.token

export default function App() {
  const [me, setMe] = useState({ display_name: '', agent_name: '' })
  const [conversations, setConversations] = useState(defaultConversations())
  const [currentConvId, setCurrentConvId] = useState('self')
  const [status, setStatus] = useState('connecting')
  const [ready, setReady] = useState(false)
  const [error, setError] = useState(null)
  // 手机端布局：'list' | 'chat'。桌面端此 state 无效（用 CSS 直接双栏）
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia('(max-width: 768px)').matches
  )
  const [mobileView, setMobileView] = useState('list')

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 768px)')
    const onChange = (e) => setIsMobile(e.matches)
    mq.addEventListener?.('change', onChange)
    return () => mq.removeEventListener?.('change', onChange)
  }, [])

  const convsRef = useRef(conversations)
  useEffect(() => { convsRef.current = conversations }, [conversations])

  const lastSentRef = useRef({})

  // ── state helpers ──
  const updateConv = useCallback((convId, updater) => {
    setConversations((prev) => {
      const conv = prev[convId]
      if (!conv) return prev
      const next = typeof updater === 'function' ? updater(conv) : updater
      return { ...prev, [convId]: { ...conv, ...next } }
    })
  }, [])

  const upsertConv = useCallback((convId, patch) => {
    setConversations((prev) => {
      const existing = prev[convId] || { id: convId, items: [], unread: 0, _pendingDetails: [] }
      return { ...prev, [convId]: { ...existing, ...patch } }
    })
  }, [])

  const appendItem = useCallback((convId, item) => {
    setConversations((prev) => {
      const conv = prev[convId]
      if (!conv) return prev
      const nextUnread = convId !== currentConvIdRef.current && item.kind !== 'system'
        ? (conv.unread || 0) + 1 : conv.unread
      return {
        ...prev,
        [convId]: { ...conv, items: [...conv.items, item], unread: nextUnread },
      }
    })
  }, [])

  const currentConvIdRef = useRef(currentConvId)
  useEffect(() => { currentConvIdRef.current = currentConvId }, [currentConvId])

  // ── bootstrap ──
  useEffect(() => {
    if (!userId || !token) {
      setError('缺少 user_id 或 token 参数')
      return
    }
    let cleanup = null
    ;(async () => {
      try {
        const cfg = await api.getConfig(userId, token)
        if (cfg.me) setMe(cfg.me)
        // 滑动续期：把服务器新签的 token 存 localStorage，下次刷新页面就是新有效期
        if (cfg.refresh_token) saveToken(userId, cfg.refresh_token)

        // 一次性构建完整的 conversations dict（默认三个 tab + 落库过的 pair）
        const convsData = await api.getConversations(userId, token)
        const list = convsData.conversations || []
        const initialConvs = defaultConversations()
        for (const tab of list) {
          if (tab.kind !== 'pair') continue
          initialConvs[tab.conv_id] = {
            id: tab.conv_id,
            kind: 'pair',
            name: `${cfg.me?.agent_name || '我的助手'} ↔ ${tab.other_display_name || tab.other_user_id}`,
            otherUid: tab.other_user_id,
            otherName: tab.other_display_name,
            myName: cfg.me?.agent_name || '我的助手',
            items: [], unread: 0, _pendingDetails: [],
            historyLoaded: false,
          }
        }

        // 并行预加载所有 tab 的历史（self / teacher / claude / 每个 pair）
        const allConvIds = Object.keys(initialConvs)
        const historyResults = await Promise.all(
          allConvIds.map(async (cid) => {
            try {
              const data = await api.getHistory(userId, token, cid, 50)
              return [cid, data.messages || []]
            } catch {
              return [cid, []]
            }
          })
        )

        // 一次性把所有历史合并进 state，之后 tab 切换零延迟
        const finalConvs = { ...initialConvs }
        for (const [cid, messages] of historyResults) {
          const conv = finalConvs[cid]
          if (!conv) continue
          const items = messages.map((m) => {
            let kind, agentName
            if (conv.kind === 'pair') {
              const from = m.metadata?.from_user_id || ''
              kind = from === userId ? 'agent_out' : 'agent_in'
              agentName = m.metadata?.from_display_name
            } else {
              kind = m.role === 'assistant' ? 'assistant'
                   : m.role === 'system' ? 'system' : 'user'
            }
            return { kind, text: m.content, time: Date.now(), historical: true, agentName }
          })
          finalConvs[cid] = { ...conv, items, historyLoaded: true }
        }
        setConversations(finalConvs)
        setReady(true)

        // 订阅 pusher
        const sub = subscribeChannel(cfg, token, handleEvent, setStatus)
        cleanup = sub.cleanup
      } catch (e) {
        console.error(e)
        // 鉴权失败：清 localStorage 避免用死的 token 反复重试
        const msg = String(e.message || e)
        if (msg.includes('token') || msg.includes('鉴权') || msg.includes('401')) {
          clearAuth(userId)
        }
        setError(msg)
      }
    })()
    return () => { if (cleanup) cleanup() }
    // eslint-disable-next-line
  }, [])

  // ── history 加载 ──
  const loadConvHistory = useCallback(async (convId) => {
    try {
      const data = await api.getHistory(userId, token, convId, 50)
      const conv = convsRef.current[convId]
      if (!conv || !data.messages) return
      // 后端返回空但本地已有实时收到的消息：不覆盖，保留内存里那批
      if (data.messages.length === 0 && conv.items && conv.items.length > 0) return
      const items = data.messages.map((m) => {
        let kind, agentName
        if (conv.kind === 'pair') {
          const from = m.metadata?.from_user_id || ''
          kind = from === userId ? 'agent_out' : 'agent_in'
          agentName = m.metadata?.from_display_name
        } else {
          kind = m.role === 'assistant' ? 'assistant'
               : m.role === 'system' ? 'system' : 'user'
        }
        return { kind, text: m.content, time: Date.now(), historical: true, agentName }
      })
      updateConv(convId, { items })
    } catch (e) {
      console.warn(`${convId} 历史加载失败`, e)
    }
  }, [updateConv])

  // ── switch conv ──
  const selectConv = useCallback(async (convId) => {
    setCurrentConvId(convId)
    updateConv(convId, { unread: 0 })
    // 手机上点某条 conv → 切换到 chat 视图
    if (window.matchMedia('(max-width: 768px)').matches) {
      setMobileView('chat')
    }
    const conv = convsRef.current[convId]
    if (conv && !conv.historyLoaded && convId !== 'self') {
      updateConv(convId, { historyLoaded: true })
      await loadConvHistory(convId)
    }
  }, [loadConvHistory, updateConv])

  // ── event dispatch ──
  function handleEvent(evt, d) {
    const text = d?.text || ''
    switch (evt) {
      case 'user_message':
        if (text && text === lastSentRef.current.self) { lastSentRef.current.self = ''; return }
        appendItem('self', { kind: 'user', text, time: Date.now() })
        break
      case 'assistant_reply': {
        const details = convsRef.current.self?._pendingDetails || []
        updateConv('self', { _pendingDetails: [] })
        appendItem('self', { kind: 'assistant', text, time: Date.now(), agentName: me.agent_name || '助手', details: details.slice() })
        break
      }
      case 'verbose_thinking':
      case 'verbose_tool_call':
      case 'verbose_tool_out': {
        const type = evt.replace('verbose_', '')
        const conv = convsRef.current.self
        const next = [...(conv?._pendingDetails || []), { type, text }]
        updateConv('self', { _pendingDetails: next })
        break
      }
      case 'system_event':
        appendItem('self', { kind: 'system', text, time: Date.now() })
        break
      case 'teacher_user_message':
        if (text && text === lastSentRef.current.teacher) { lastSentRef.current.teacher = ''; return }
        appendItem('teacher', { kind: 'user', text, time: Date.now() })
        updateConv('teacher', { _pendingDetails: [] })
        break
      case 'teacher_reply': {
        const details = convsRef.current.teacher?._pendingDetails || []
        updateConv('teacher', { _pendingDetails: [] })
        appendItem('teacher', { kind: 'assistant', text, time: Date.now(), agentName: '老师', details: details.slice() })
        break
      }
      case 'teacher_thinking':
      case 'teacher_tool_call':
      case 'teacher_tool_out': {
        const type = evt.replace('teacher_', '')
        const conv = convsRef.current.teacher
        const next = [...(conv?._pendingDetails || []), { type, text }]
        updateConv('teacher', { _pendingDetails: next })
        break
      }
      case 'claude_user_message':
        if (text && text === lastSentRef.current.claude) { lastSentRef.current.claude = ''; return }
        appendItem('claude', { kind: 'user', text, time: Date.now() })
        break
      case 'claude_reply':
        appendItem('claude', { kind: 'assistant', text, time: Date.now(), agentName: 'Claude' })
        break
      case 'agent_conversation':
        handleAgentConv(d)
        break
      default: break
    }
  }

  function handleAgentConv(data) {
    const otherUid = data.from_user_id === userId ? data.to_user_id : data.from_user_id
    const otherName = data.from_user_id === userId ? data.to : data.from
    const myName = data.from_user_id === userId ? data.from : data.to
    const convId = `pair-${otherUid}`
    if (!convsRef.current[convId]) {
      // 实时创建的 pair 直接标 historyLoaded：消息本身就是实时来的，
      // 用户点 tab 时不用再拉后端历史（避免空数组覆盖内存里的实时消息）
      upsertConv(convId, {
        id: convId, kind: 'pair',
        name: `${myName || '我的助手'} ↔ ${otherName}`,
        otherUid, otherName, myName,
        items: [], unread: 0, _pendingDetails: [], historyLoaded: true,
      })
    }
    const isMineOut = data.from_user_id === userId
    if (isMineOut && data.text && data.text === lastSentRef.current[convId]) {
      lastSentRef.current[convId] = ''
      return
    }
    appendItem(convId, {
      kind: isMineOut ? 'agent_out' : 'agent_in',
      text: data.text || '', agentName: data.from, time: Date.now(),
    })
  }

  // ── 发送 ──
  const send = async (msg) => {
    lastSentRef.current[currentConvId] = msg
    // optimistic
    if (currentConvId === 'self' || currentConvId === 'teacher' || currentConvId === 'claude') {
      appendItem(currentConvId, { kind: 'user', text: msg, time: Date.now() })
    } else if (currentConvId.startsWith('pair-')) {
      appendItem(currentConvId, { kind: 'agent_out', text: msg, time: Date.now(), agentName: me.agent_name || '我的助手' })
    }
    await api.sendMessage(userId, token, currentConvId, msg)
  }

  // ── UI ──
  if (error) {
    return (
      <div style={{ padding: 40, textAlign: 'center' }}>
        <Title heading={4} type="danger">❌ {error}</Title>
        <Text type="tertiary">请在微信里向 bot 发一句「/看板」重新拿链接</Text>
      </div>
    )
  }
  if (!ready) {
    return (
      <div style={{ height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin size="large" tip="加载中..." />
      </div>
    )
  }

  const currentConv = conversations[currentConvId]
  const participants = participantsOf(currentConv, me)

  // 手机上根据 mobileView 决定显示哪一栏；桌面上并排
  const showSider = !isMobile || mobileView === 'list'
  const showChat = !isMobile || mobileView === 'chat'

  return (
    <Layout style={{ height: '100vh', background: 'var(--bg-chat)' }}>
      {showSider && (
        <Sider style={{
          width: isMobile ? '100%' : 280,
          background: '#fff',
          flex: isMobile ? '1 1 100%' : '0 0 280px',
          maxWidth: isMobile ? '100%' : 280,
        }}>
          <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--semi-color-border)' }}>
            <Title heading={5} style={{ margin: 0, fontSize: 16 }}>
              会话（{Object.keys(conversations).length}）
            </Title>
            <Text size="small" type="tertiary">
              {me.display_name || userId} · pair {Object.values(conversations).filter(c => c.kind === 'pair').length}
            </Text>
          </div>
          <Sidebar
            conversations={conversations}
            currentConvId={currentConvId}
            onSelect={selectConv}
            status={status}
          />
        </Sider>
      )}
      {showChat && (
        <Layout>
          <Header style={{
            background: '#fff',
            borderBottom: '1px solid var(--semi-color-border)',
            padding: '10px 16px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 8,
            height: 56,
            flexShrink: 0,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, flex: 1 }}>
              {isMobile && (
                <Button
                  icon={<IconChevronLeft />}
                  theme="borderless"
                  type="tertiary"
                  onClick={() => setMobileView('list')}
                  style={{ flexShrink: 0 }}
                />
              )}
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 15, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {currentConv?.name}
                </div>
                <div style={{ fontSize: 12, color: 'var(--semi-color-text-2)' }}>
                  {currentConv?.kind === 'teacher' && '苏格拉底 + 费曼'}
                  {currentConv?.kind === 'claude' && '本地 Claude Code CLI'}
                  {currentConv?.kind === 'pair' && '跨 Agent 对话'}
                </div>
              </div>
            </div>
            <ParticipantsGroup participants={participants} />
          </Header>
          <Content style={{
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
            minHeight: 0,
            flex: 1,
          }}>
            <MessageList conv={currentConv} me={me} />
            <Composer onSend={send} disabled={!currentConv} />
          </Content>
        </Layout>
      )}
    </Layout>
  )
}
