import { useState } from 'react'
import { TextArea, Button, Toast } from '@douyinfe/semi-ui'
import { IconSend } from '@douyinfe/semi-icons'

export default function Composer({ onSend, disabled }) {
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)

  const submit = async () => {
    const t = text.trim()
    if (!t || sending || disabled) return
    setSending(true)
    try {
      await onSend(t)
      setText('')
    } catch (e) {
      Toast.error(String(e.message || e))
    } finally {
      setSending(false)
    }
  }

  return (
    <div style={{
      padding: '10px 16px',
      borderTop: '1px solid var(--semi-color-border)',
      background: '#fff',
      display: 'flex',
      gap: 8,
      alignItems: 'flex-end',
      flexShrink: 0,
    }}>
      <TextArea
        value={text}
        onChange={setText}
        placeholder="输入消息，Ctrl/⌘ + Enter 发送"
        autosize={{ minRows: 1, maxRows: 5 }}
        rows={1}
        style={{ flex: 1, fontSize: 14, resize: 'none' }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault()
            submit()
          }
        }}
      />
      <Button
        icon={<IconSend />}
        theme="solid"
        type="primary"
        loading={sending}
        onClick={submit}
        disabled={disabled || !text.trim()}
      >
        发送
      </Button>
    </div>
  )
}
