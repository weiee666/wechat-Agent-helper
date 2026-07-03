// Claude 头像：Anthropic 品牌暖橙色 + 白色 C（星芒风格）
export default function ClaudeAvatar({ size = 32 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="ca-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#E8A87C" />
          <stop offset="1" stopColor="#D97757" />
        </linearGradient>
      </defs>
      <circle cx="20" cy="20" r="20" fill="url(#ca-bg)" />
      {/* Anthropic 风格星芒 */}
      <path
        d="M 20 8
           L 21.6 17.4
           L 31 19
           L 21.6 20.6
           L 20 30
           L 18.4 20.6
           L 9 19
           L 18.4 17.4
           Z"
        fill="#fff"
      />
    </svg>
  )
}
