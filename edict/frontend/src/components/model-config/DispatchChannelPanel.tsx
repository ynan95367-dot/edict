import { CHANNELS } from './modelConfigUtils';

type DispatchChannelPanelProps = {
  channelSel: string;
  currentChannel?: string;
  channelStatus: string;
  isOpenCodeRuntime: boolean;
  onSelect: (channel: string) => void;
  onApply: () => void;
};

export function DispatchChannelPanel({
  channelSel,
  currentChannel,
  channelStatus,
  isOpenCodeRuntime,
  onSelect,
  onApply,
}: DispatchChannelPanelProps) {
  return (
    <div style={{ marginTop: 24, marginBottom: 8 }}>
      <div className="sec-title">{isOpenCodeRuntime ? 'OpenClaw 通知渠道' : '交办渠道'}</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 0' }}>
        <select
          className="msel"
          value={channelSel}
          onChange={(e) => onSelect(e.target.value)}
          style={{ maxWidth: 220 }}
          disabled={isOpenCodeRuntime}
        >
          {CHANNELS.map((ch) => (
            <option key={ch.id} value={ch.id}>{ch.label}</option>
          ))}
        </select>
        <button
          className="btn btn-p"
          disabled={isOpenCodeRuntime || channelSel === (currentChannel || 'feishu')}
          onClick={onApply}
        >
          应用
        </button>
        {channelStatus && (
          <span style={{ fontSize: 12, color: channelStatus.startsWith('✅') ? 'var(--success)' : 'var(--danger)' }}>
            {channelStatus}
          </span>
        )}
      </div>
      <div style={{ fontSize: 11, color: 'var(--muted)' }}>
        {isOpenCodeRuntime
          ? '当前为 OpenCode 模式：任务执行通过 Outbox + OpenCode CLI 处理，这里的渠道仅用于 OpenClaw deliver，不影响 OpenCode 执行成功率。'
          : '自动交办时使用的通知渠道；OpenClaw 模式需在 openclaw.json 中配置对应 channel。'}
      </div>
    </div>
  );
}
