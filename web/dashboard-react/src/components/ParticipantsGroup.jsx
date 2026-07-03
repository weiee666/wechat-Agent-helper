import { AvatarGroup, Avatar, Tooltip } from '@douyinfe/semi-ui'
import { renderAvatar } from '../avatars/registry.jsx'

/**
 * 右上角 channel 参与者：Semi AvatarGroup 重叠展示。
 * hover 圆圈显示名字（Tooltip）。
 */
export default function ParticipantsGroup({ participants, size = 32 }) {
  if (!participants || participants.length === 0) return null
  return (
    <AvatarGroup overlapFrom="end" size="small" maxCount={5}>
      {participants.map((p, i) => (
        <Tooltip key={`${p.role}-${i}`} content={p.name} position="bottom">
          <Avatar
            size="small"
            style={{
              backgroundColor: 'transparent',
              border: '2px solid #fff',
              overflow: 'hidden',
            }}
          >
            {renderAvatar(p.role, size)}
          </Avatar>
        </Tooltip>
      ))}
    </AvatarGroup>
  )
}
