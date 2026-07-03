import { Badge, Typography } from '@douyinfe/semi-ui'
import { renderAvatar } from '../avatars/registry.jsx'

const { Text } = Typography

function convToRole(conv) {
  if (conv.kind === 'teacher') return 'teacher'
  if (conv.kind === 'claude') return 'claude'
  if (conv.kind === 'pair') return 'peer_bot'
  return 'bot'
}

function ConvItem({ conv, active, onClick }) {
  const last = conv.items && conv.items.length > 0
    ? (conv.items[conv.items.length - 1].text || '')
    : ''
  return (
    <div
      onClick={onClick}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '10px 12px',
        margin: '2px 8px',
        borderRadius: 8,
        cursor: 'pointer',
        background: active ? 'var(--semi-color-fill-0)' : 'transparent',
        transition: 'background 0.15s',
      }}
      onMouseEnter={(e) => {
        if (!active) e.currentTarget.style.background = 'var(--semi-color-fill-0)'
      }}
      onMouseLeave={(e) => {
        if (!active) e.currentTarget.style.background = 'transparent'
      }}
    >
      <div style={{ width: 36, height: 36, flexShrink: 0 }}>
        {renderAvatar(convToRole(conv), 36)}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 14, fontWeight: 500,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          color: 'var(--semi-color-text-0)',
        }}>
          {conv.name}
        </div>
        <Text size="small" type="tertiary" ellipsis={{ showTooltip: false }} style={{ fontSize: 12 }}>
          {last ? last.slice(0, 26) : '暂无消息'}
        </Text>
      </div>
      {conv.unread > 0 && (
        <Badge count={conv.unread} type="danger" style={{ flexShrink: 0 }} />
      )}
    </div>
  )
}

export default function Sidebar({ conversations, currentConvId, onSelect, status }) {
  const list = Object.values(conversations)
  return (
    <div style={{
      height: '100%',
      display: 'flex',
      flexDirection: 'column',
      background: '#fff',
      borderRight: '1px solid var(--semi-color-border)',
    }}>
      <div style={{ flex: 1, overflowY: 'auto', padding: '6px 0' }}>
        {list.map((c) => (
          <ConvItem
            key={c.id}
            conv={c}
            active={c.id === currentConvId}
            onClick={() => onSelect(c.id)}
          />
        ))}
      </div>
      <div style={{
        padding: '10px 16px',
        borderTop: '1px solid var(--semi-color-border)',
        fontSize: 12,
        color: 'var(--semi-color-text-2)',
        display: 'flex',
        alignItems: 'center',
      }}>
        <span
          style={{
            display: 'inline-block',
            width: 8, height: 8, borderRadius: 4,
            background: status === 'connected' ? '#00B42A' : '#F53F3F',
            marginRight: 6,
          }}
        />
        {status === 'connected' ? '实时连接' : (status || '未连接')}
      </div>
    </div>
  )
}
