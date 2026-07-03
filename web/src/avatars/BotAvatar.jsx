// 助手头像：飞书主题色（品牌蓝）+ 白色简机器人
export default function BotAvatar({ size = 32 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="ba-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#00D6B9" />
          <stop offset="1" stopColor="#00A0C4" />
        </linearGradient>
      </defs>
      <circle cx="20" cy="20" r="20" fill="url(#ba-bg)" />
      {/* 天线 */}
      <circle cx="20" cy="9" r="1.6" fill="#fff" />
      <line x1="20" y1="10.6" x2="20" y2="14" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" />
      {/* 头 */}
      <rect x="10.5" y="14" width="19" height="15" rx="4" fill="#fff" />
      {/* 眼睛 */}
      <circle cx="16" cy="21" r="1.9" fill="#00A0C4" />
      <circle cx="24" cy="21" r="1.9" fill="#00A0C4" />
      {/* 嘴 */}
      <rect x="16.5" y="25" width="7" height="1.6" rx="0.8" fill="#00A0C4" />
    </svg>
  )
}
