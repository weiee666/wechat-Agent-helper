import { useEffect, useRef, useState, useCallback } from 'react'
import { Layout, Typography, Toast, Spin } from '@douyinfe/semi-ui'
import * as api from './api.js'
import { subscribeChannel } from './pusher.js'
import { defaultConversations, participantsOf } from './state.js'
import Sidebar from './components/Sidebar.jsx'
import MessageList from './components/MessageList.jsx'
import Composer from './components/Composer.jsx'
import ParticipantsGroup from './components/ParticipantsGroup.jsx'

const { Header, Sider, Content } = Layout
const { Title, Text } = Typography

const params = new URLSearchParams(location.search)
const userId = params.get('user_id') || ''
const token = params.get('token') || ''

export default function App() {
  const [me, setMe] = useState({ display_name: '', agent_name: '' })
  const [conversations, setConversations] = useState(defaultConversations())
  const [currentConvId, setCurrentConvId] = useState('self')
  const [status, setStatus] = useState('connecting')
  const [ready, setReady] = useState(false)
  const [error, setError] = useState(null)

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

        // 拉 tab 列表：恢复 pair conv
        const convsData = await api.getConversations(userId, token)
        const list = convsData.conversations || []
        for (const tab of list) {
          if (tab.kind !== 'pair') continue
          const cid = tab.conv_id
          upsertConv(cid, {
            id: cid,
            kind: 'pair',
            name: `${cfg.me?.agent_name || '我的助手'} ↔ ${tab.other_display_name || tab.other_user_id}`,
            otherUid: tab.other_user_id,
            otherName: tab.other_display_name,
            myName: cfg.me?.agent_name || '我的助手',
            items: [],
            unread: 0,
            _pendingDetails: [],
            historyLoaded: false,
          })
        }

        // 先拉 self 历史
        await loadConvHistory('self')
        setReady(true)

        // 订阅 pusher
        const sub = subscribeChannel(cfg, token, handleEvent, setStatus)
        cleanup = sub.cleanup
      } catch (e) {
        console.error(e)
        setError(e.message || String(e))
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
      upsertConv(convId, {
        id: convId, kind: 'pair',
        name: `${myName || '我的助手'} ↔ ${otherName}`,
        otherUid, otherName, myName,
        items: [], unread: 0, _pendingDetails: [], historyLoaded: false,
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

  return (
    <Layout style={{ height: '100vh', background: 'var(--bg-chat)' }}>
      <Sider style={{ width: 280, background: '#fff', flex: '0 0 280px' }}>
        <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--semi-color-border)' }}>
          <Title heading={5} style={{ margin: 0, fontSize: 16 }}>会话</Title>
          <Text size="small" type="tertiary">{me.display_name || userId}</Text>
        </div>
        <Sidebar
          conversations={conversations}
          currentConvId={currentConvId}
          onSelect={selectConv}
          status={status}
        />
      </Sider>
      <Layout>
        <Header style={{
          background: '#fff',
          borderBottom: '1px solid var(--semi-color-border)',
          padding: '10px 20px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          height: 56,
        }}>
          <div>
            <div style={{ fontSize: 15, fontWeight: 600 }}>{currentConv?.name}</div>
            <div style={{ fontSize: 12, color: 'var(--semi-color-text-2)' }}>
              {currentConv?.kind === 'teacher' && '苏格拉底 + 费曼'}
              {currentConv?.kind === 'claude' && '本地 Claude Code CLI'}
              {currentConv?.kind === 'pair' && '跨 Agent 对话'}
            </div>
          </div>
          <ParticipantsGroup participants={participants} />
        </Header>
        <Content style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <MessageList conv={currentConv} me={me} />
          <Composer onSend={send} disabled={!currentConv} />
        </Content>
      </Layout>
    </Layout>
  )
}
