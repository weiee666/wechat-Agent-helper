// 老师头像：紫色 + 学位帽/书
export default function TeacherAvatar({ size = 32 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="ta-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#8B5CF6" />
          <stop offset="1" stopColor="#6D28D9" />
        </linearGradient>
      </defs>
      <circle cx="20" cy="20" r="20" fill="url(#ta-bg)" />
      {/* 学位帽帽顶（菱形） */}
      <path d="M 20 10 L 32 15 L 20 20 L 8 15 Z" fill="#fff" />
      {/* 帽穗 */}
      <line x1="32" y1="15" x2="32" y2="22" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="32" cy="23" r="1.4" fill="#fff" />
      {/* 帽底部（书本） */}
      <path
        d="M 12 20 v 5 c 0 2 3.5 3.5 8 3.5 s 8-1.5 8-3.5 v -5"
        fill="#fff"
        opacity="0.9"
      />
    </svg>
  )
}
