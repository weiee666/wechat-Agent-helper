import Pusher from 'pusher-js'

/**
 * 建立 Pusher 私有 channel 订阅。
 * @param {object} cfg  /config 返回值：{pusher_key, pusher_cluster, channel}
 * @param {string} token  用于 pusher-auth 签名
 * @param {function} onEvent  (eventName, data) => void
 * @returns { channel, pusher, cleanup } —— cleanup 关闭 subscription
 */
export function subscribeChannel(cfg, token, onEvent, onStatus) {
  const pusher = new Pusher(cfg.pusher_key, {
    cluster: cfg.pusher_cluster,
    authEndpoint: `/api/dashboard/pusher-auth?token=${encodeURIComponent(token)}`,
  })
  pusher.connection.bind('connected', () => onStatus?.('connected'))
  pusher.connection.bind('disconnected', () => onStatus?.('disconnected'))
  pusher.connection.bind('error', () => onStatus?.('error'))

  const channel = pusher.subscribe(cfg.channel)
  channel.bind('pusher:subscription_error', (e) => {
    console.error('subscribe failed', e)
    onStatus?.('subscribe_error')
  })

  const events = [
    'user_message', 'assistant_reply',
    'verbose_thinking', 'verbose_tool_call', 'verbose_tool_out',
    'system_event', 'agent_conversation',
    'teacher_user_message', 'teacher_reply',
    'teacher_thinking', 'teacher_tool_call', 'teacher_tool_out',
    'claude_user_message', 'claude_reply',
  ]
  events.forEach((e) => channel.bind(e, (d) => onEvent(e, d)))

  return {
    channel,
    pusher,
    cleanup() {
      try { pusher.unsubscribe(cfg.channel) } catch {}
      try { pusher.disconnect() } catch {}
    },
  }
}
