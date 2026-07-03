import { useEffect, useRef } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { renderAvatar } from '../avatars/registry.jsx'

marked.setOptions({ breaks: true, gfm: true })
function md(text) {
  if (!text) return ''
  try {
    return DOMPurify.sanitize(marked.parse(text), { ADD_ATTR: ['target'] })
  } catch {
    const div = document.createElement('div')
    div.textContent = text
    return div.innerHTML.replace(/\n/g, '<br>')
  }
}

function fmtTime(ts) {
  const d = ts ? new Date(ts) : new Date()
  return d.toLocaleTimeString('zh-CN', { hour12: false })
}

// 消息 item.kind ∈ 'user'|'assistant'|'system'|'agent_out'|'agent_in'
export default function MessageList({ conv, me }) {
  const scrollRef = useRef(null)
  const items = conv?.items || []

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [items.length])

  if (!conv) return null

  return (
    <div
      ref={scrollRef}
      style={{
        flex: 1,
        minHeight: 0,
        overflowY: 'auto',
        overflowX: 'hidden',
        padding: '16px 24px',
        background: 'var(--bg-chat)',
        WebkitOverflowScrolling: 'touch',
        overscrollBehavior: 'contain',
      }}
    >
      <div style={{ maxWidth: 780, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 10 }}>
        {items.map((it, i) => (
          <MessageRow key={i} item={it} conv={conv} me={me} />
        ))}
        {items.length === 0 && (
          <div style={{ color: 'var(--semi-color-text-2)', fontSize: 13, textAlign: 'center', marginTop: 40 }}>
            暂无消息
          </div>
        )}
      </div>
    </div>
  )
}

function MessageRow({ item, conv, me }) {
  const isMine = item.kind === 'user' || item.kind === 'agent_out'
  const isSystem = item.kind === 'system'
  const isPair = conv.kind === 'pair'

  if (isSystem) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center' }}>
        <div style={{
          background: 'rgba(0,0,0,0.06)', color: 'var(--semi-color-text-2)',
          fontSize: 12, borderRadius: 12, padding: '6px 12px', maxWidth: '90%',
        }}>
          {item.text}
        </div>
      </div>
    )
  }

  // 头像 role 判定
  let leftRole = 'bot'
  if (conv.kind === 'teacher') leftRole = 'teacher'
  else if (conv.kind === 'claude') leftRole = 'claude'
  else if (isPair) leftRole = 'peer_bot'
  const rightRole = isPair ? 'bot' : 'user'

  const bubbleStyle = isMine ? {
    background: 'var(--bubble-me)',
    color: '#fff',
    borderRadius: '14px 4px 14px 14px',
  } : {
    background: 'var(--bubble-other)',
    color: 'var(--semi-color-text-0)',
    borderRadius: '4px 14px 14px 14px',
  }

  return (
    <div style={{
      display: 'flex',
      flexDirection: isMine ? 'row-reverse' : 'row',
      alignItems: 'flex-start',
      gap: 8,
    }}>
      <div style={{ width: 34, height: 34, flexShrink: 0 }}>
        {renderAvatar(isMine ? rightRole : leftRole, 34)}
      </div>
      <div style={{ maxWidth: '68%', display: 'flex', flexDirection: 'column', gap: 4 }}>
        {item.agentName && !isMine && (
          <div style={{ fontSize: 11, color: 'var(--semi-color-text-2)', marginLeft: 8 }}>
            {item.agentName}
          </div>
        )}
        <div
          style={{
            padding: '9px 13px',
            fontSize: 14,
            lineHeight: 1.55,
            wordBreak: 'break-word',
            ...bubbleStyle,
          }}
          dangerouslySetInnerHTML={{ __html: md(item.text) }}
        />
        {item.details && item.details.length > 0 && (
          <details style={{ marginLeft: 8 }}>
            <summary style={{ fontSize: 11, color: 'var(--semi-color-text-2)', cursor: 'pointer' }}>
              {item.details.length} 条思考/工具事件
            </summary>
            <div style={{ marginTop: 4, fontSize: 11, color: 'var(--semi-color-text-2)', background: 'rgba(0,0,0,0.03)', padding: 8, borderRadius: 6 }}>
              {item.details.map((d, i) => (
                <div key={i} style={{ padding: '2px 0' }}>
                  <b style={{ opacity: 0.7 }}>{d.type}</b>: {d.text}
                </div>
              ))}
            </div>
          </details>
        )}
        <div style={{ fontSize: 10, color: 'var(--semi-color-text-3)', textAlign: isMine ? 'right' : 'left', paddingLeft: 4, paddingRight: 4 }}>
          {fmtTime(item.time)}
        </div>
      </div>
    </div>
  )
}
