// 用外部 CDN 头像替换手画 SVG。
// - DiceBear（notionists-neutral / bottts-neutral 风格 + 稳定 seed + 渐变背景色区分角色）
// - Claude 用 Anthropic 官方 apple-touch-icon
// - 加载失败降级为纯色圆 + 首字母

// micah（扁平设计感人像）+ bottts-neutral（简约机器人）+ initials（大写字母 avatar，Claude 用）
const AVATAR_URL = {
  user:     'https://api.dicebear.com/9.x/micah/svg?seed=Weibo&backgroundColor=b6e3f4,c0aede&backgroundType=gradientLinear',
  bot:      'https://api.dicebear.com/9.x/bottts-neutral/svg?seed=Assistant&backgroundColor=00d6b9,4ecdc4&backgroundType=gradientLinear',
  teacher:  'https://api.dicebear.com/9.x/micah/svg?seed=Professor&backgroundColor=c084fc,a78bfa&backgroundType=gradientLinear',
  claude:   'https://api.dicebear.com/9.x/initials/svg?seed=Claude&backgroundColor=D97757&fontWeight=600&textColor=ffffff',
  peer_bot: 'https://api.dicebear.com/9.x/bottts-neutral/svg?seed=Peer&backgroundColor=fca5a5,f87171&backgroundType=gradientLinear',
}

const FALLBACK_BG = {
  user: '#3370FF',
  bot: '#00A0C4',
  teacher: '#8B5CF6',
  claude: '#D97757',
  peer_bot: '#F87171',
}

export function renderAvatar(role, size = 32) {
  const url = AVATAR_URL[role] || AVATAR_URL.bot
  const bg = FALLBACK_BG[role] || FALLBACK_BG.bot
  return (
    <img
      src={url}
      alt={role}
      width={size}
      height={size}
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        display: 'block',
        objectFit: 'cover',
        background: bg,
      }}
      referrerPolicy="no-referrer"
      loading="lazy"
    />
  )
}

// 兼容旧 API（避免 breakage）
export const AvatarByRole = {}
