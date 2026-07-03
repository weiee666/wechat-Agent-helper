// 会话状态：conversations 是 { convId: Conversation } 映射
// Conversation = {
//   id, name, kind ('self'|'teacher'|'claude'|'pair'),
//   items: [{ kind, text, time, agentName?, details?, historical? }],
//   unread, _pendingDetails?
// }

export function defaultConversations() {
  const mk = (extra) => ({ items: [], unread: 0, _pendingDetails: [], ...extra })
  return {
    self:    mk({ id: 'self',    name: '我和助手',  kind: 'self' }),
    teacher: mk({ id: 'teacher', name: '老师 Agent', kind: 'teacher', historyLoaded: false }),
    claude:  mk({ id: 'claude',  name: 'Claude',    kind: 'claude',  historyLoaded: false }),
  }
}

export function convDisplayName(conv, me) {
  if (!conv) return ''
  if (conv.kind === 'self') return conv.name
  return conv.name
}

// 该 conv 里有谁（用于右上角 AvatarGroup）
// 返回 [{ role: 'user'|'bot'|'teacher'|'claude'|'peer_bot', name, side }]
export function participantsOf(conv, me) {
  if (!conv) return []
  const myName = me?.display_name || '我'
  const myAgentName = me?.agent_name || '我的助手'
  const meP = { role: 'user', name: myName, side: 'me' }
  if (conv.kind === 'self') {
    return [meP, { role: 'bot', name: myAgentName, side: 'peer' }]
  }
  if (conv.kind === 'teacher') {
    return [meP, { role: 'teacher', name: '老师', side: 'peer' }]
  }
  if (conv.kind === 'claude') {
    return [meP, { role: 'claude', name: 'Claude', side: 'peer' }]
  }
  if (conv.kind === 'pair') {
    return [
      { role: 'bot', name: conv.myName || myAgentName, side: 'me' },
      { role: 'peer_bot', name: conv.otherName || '对方助手', side: 'peer' },
    ]
  }
  return [meP]
}
