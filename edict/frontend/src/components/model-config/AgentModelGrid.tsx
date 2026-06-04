import type { AgentConfig, ModelHealthAgent, ModelRegistryEntry } from '../../api';
import { formatLatency, providerForModel, statusClass, type ModelOption } from './modelConfigUtils';

type AgentModelGridProps = {
  agentConfig: AgentConfig;
  models: ModelOption[];
  selMap: Record<string, string>;
  statusMap: Record<string, { cls: string; text: string }>;
  healthByAgent: Record<string, ModelHealthAgent>;
  registryById: Record<string, ModelRegistryEntry>;
  onSelect: (agentId: string, model: string) => void;
  onApply: (agentId: string) => void;
  onReset: (agentId: string) => void;
};

export function AgentModelGrid({
  agentConfig,
  models,
  selMap,
  statusMap,
  healthByAgent,
  registryById,
  onSelect,
  onApply,
  onReset,
}: AgentModelGridProps) {
  return (
    <div className="model-grid">
      {agentConfig.agents.map((ag) => {
        const sel = selMap[ag.id] || ag.model;
        const changed = sel !== ag.model;
        const st = statusMap[ag.id];
        const h = healthByAgent[ag.id];
        const currentRegistry = registryById[ag.model];
        const cls = statusClass(h?.status);
        return (
          <div className="mc-card" key={ag.id}>
            <div className="mc-top">
              <span className="mc-emoji">{ag.emoji || '🏛️'}</span>
              <div>
                <div className="mc-name">
                  {ag.label}{' '}
                  <span style={{ fontSize: 11, color: 'var(--muted)' }}>{ag.id}</span>
                </div>
                <div className="mc-role">{ag.role}</div>
              </div>
              <span className={`mh-pill mini ${cls}`}>{h?.statusLabel || '暂无观测'}</span>
            </div>
            <div className="mc-cur">
              当前: <b>{ag.model}</b>
            </div>
            <div className="mc-health-line">
              <span>{h?.provider || providerForModel(ag.model)}</span>
              <span>{h?.tierLabel || '未知等级'}</span>
              <span>延迟 {formatLatency(currentRegistry?.latencyMs ?? h?.lastLatencyMs)}</span>
              <span>失败 {h?.failureCount || 0}</span>
              <span>超时 {h?.timeoutCount || 0}</span>
            </div>
            <select className="msel" value={sel} onChange={(e) => onSelect(ag.id, e.target.value)}>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.l} ({m.p}) · {formatLatency(m.latencyMs)}
                </option>
              ))}
            </select>
            <div className="mc-btns">
              <button className="btn btn-p" disabled={!changed} onClick={() => onApply(ag.id)}>
                应用
              </button>
              <button className="btn btn-g" onClick={() => onReset(ag.id)}>
                重置
              </button>
            </div>
            {st && <div className={`mc-st ${st.cls}`}>{st.text}</div>}
          </div>
        );
      })}
    </div>
  );
}
