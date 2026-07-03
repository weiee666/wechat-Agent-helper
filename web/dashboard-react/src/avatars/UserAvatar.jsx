// 用户头像：蓝色圆形 + 白色人像剪影
export default function UserAvatar({ size = 32 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="ua-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#5B8FF9" />
          <stop offset="1" stopColor="#3370FF" />
        </linearGradient>
      </defs>
      <circle cx="20" cy="20" r="20" fill="url(#ua-bg)" />
      <circle cx="20" cy="15" r="5.5" fill="#fff" />
      <path
        d="M20 22 c-6 0-10 3.6-10 8 v 4 h 20 v -4 c 0-4.4-4-8-10-8z"
        fill="#fff"
      />
    </svg>
  )
}
