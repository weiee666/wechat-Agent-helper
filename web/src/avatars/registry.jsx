import UserAvatar from './UserAvatar.jsx'
import BotAvatar from './BotAvatar.jsx'
import TeacherAvatar from './TeacherAvatar.jsx'
import ClaudeAvatar from './ClaudeAvatar.jsx'

// 角色 → 头像组件。size 传入 render 时决定。
export const AvatarByRole = {
  user: UserAvatar,
  bot: BotAvatar,        // 助手
  teacher: TeacherAvatar,
  claude: ClaudeAvatar,
  // pair 场景对方助手默认用 bot
  peer_bot: BotAvatar,
}

export function renderAvatar(role, size = 32) {
  const Comp = AvatarByRole[role] || BotAvatar
  return <Comp size={size} />
}
