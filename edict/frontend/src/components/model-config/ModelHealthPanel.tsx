import type { AgentConfig, ModelHealthAgent, ModelHealthData } from '../../api';
import { labelForModel, providerForModel, shortTime, statusClass, STATUS_TEXT } from './modelConfigUtils';

type ModelHealthPanelProps = {
  agentConfig: AgentConfig;
  health: ModelHealthData | null;
  healthByAgent: Record<string, ModelHealthAgent>;
  healthError: string;
  healthLoading: boolean;
  onRefresh: () => void;
};

export function ModelHealthPanel({
  agentConfig,
  health,
  healthByAgent,
  healthError,
  healthLoading,
  onRefresh,
}: ModelHealthPanelProps) {
  const summary = health?.summary || {};
  const unhealthyCount = (summary.timeout || 0) + (summary.failed || 0) + (summary.degraded || 0) + (summary.offline || 0);

  return (
    <div className="model-health-panel">
      <div className="mh-head">
        <div>
          <div className="sec-title">模型连接状态</div>
          <div className="mh-sub">
            {health?.runtimeLabel || '运行时'} 观测面板 · 基于真实派发、超时和 session 错误回写
          </div>
        </div>
        <button className="btn btn-g" onClick={onRefresh} disabled={healthLoading}>
          {healthLoading ? '刷新中' : '刷新状态'}
        </button>
      </div>

      <div className="mh-overview">
        <div className={`mh-gateway ${health?.gateway?.status || 'unknown'}`}>
          <span>运行时</span>
          <b>{health?.gateway?.alive ? (health.gateway.probe ? '可用' : '降级') : '离线'}</b>
        </div>
        <div className="mh-stat ok"><span>正常</span><b>{summary.ok || 0}</b></div>
        <div className="mh-stat timeout"><span>超时</span><b>{summary.timeout || 0}</b></div>
        <div className="mh-stat failed"><span>失败/降级</span><b>{(summary.failed || 0) + (summary.degraded || 0)}</b></div>
        <div className="mh-stat unknown"><span>暂无观测</span><b>{summary.unknown || 0}</b></div>
        <div className={unhealthyCount ? 'mh-risk bad' : 'mh-risk'}>
          <span>自动替换</span>
          <b>{health?.failovers?.length || 0}</b>
        </div>
      </div>

      {healthError && <div className="mh-error">{healthError}</div>}

      <div className="mh-table">
        <div className="mh-row mh-row-head">
          <span>Agent</span>
          <span>当前模型</span>
          <span>状态</span>
          <span>最近证据</span>
          <span>同级备用</span>
        </div>
        {agentConfig.agents.map((ag) => {
          const h = healthByAgent[ag.id];
          const cls = statusClass(h?.status);
          return (
            <div className="mh-row" key={ag.id}>
              <div className="mh-agent">
                <span className="mh-agent-icon">{ag.emoji || '🏛️'}</span>
                <div>
                  <b>{ag.label}</b>
                  <small>{ag.id}</small>
                </div>
              </div>
              <div className="mh-model">
                <b>{h?.modelLabel || labelForModel(ag.model)}</b>
                <small>{h?.provider || providerForModel(ag.model)} · {h?.tierLabel || '未知等级'}</small>
              </div>
              <div>
                <span className={`mh-pill ${cls}`}>{h?.statusLabel || STATUS_TEXT.unknown}</span>
              </div>
              <div className="mh-evidence">
                <b>{h?.lastFailureAt ? shortTime(h.lastFailureAt) : h?.lastSuccessAt ? shortTime(h.lastSuccessAt) : '无记录'}</b>
                <small title={h?.lastError || ''}>{h?.lastError || (h?.source === 'config' ? '尚未发生派发观测' : '最近无错误')}</small>
              </div>
              <div className="mh-fallback">
                {h?.fallbackModel ? (
                  <>
                    <b>{h.fallbackLabel || labelForModel(h.fallbackModel)}</b>
                    <small>{h.fallbackModel}</small>
                  </>
                ) : (
                  <span>暂无同级备用</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
