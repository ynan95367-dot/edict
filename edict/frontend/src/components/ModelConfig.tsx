import { useEffect, useMemo, useState } from 'react';
import { useStore } from '../store';
import { api, type AgentConfig, type ModelHealthAgent, type ModelHealthData } from '../api';

const FALLBACK_MODELS = [
  { id: 'anthropic/claude-sonnet-4-6', l: 'Claude Sonnet 4.6', p: 'Anthropic' },
  { id: 'anthropic/claude-opus-4-5', l: 'Claude Opus 4.5', p: 'Anthropic' },
  { id: 'anthropic/claude-haiku-3-5', l: 'Claude Haiku 3.5', p: 'Anthropic' },
  { id: 'openai/gpt-4o', l: 'GPT-4o', p: 'OpenAI' },
  { id: 'openai/gpt-4o-mini', l: 'GPT-4o Mini', p: 'OpenAI' },
  { id: 'google/gemini-2.5-pro', l: 'Gemini 2.5 Pro', p: 'Google' },
  { id: 'copilot/claude-sonnet-4', l: 'Claude Sonnet 4', p: 'Copilot' },
  { id: 'copilot/claude-opus-4.5', l: 'Claude Opus 4.5', p: 'Copilot' },
  { id: 'copilot/gpt-4o', l: 'GPT-4o', p: 'Copilot' },
  { id: 'copilot/gemini-2.5-pro', l: 'Gemini 2.5 Pro', p: 'Copilot' },
];

const CHANNELS = [
  { id: 'feishu', label: '飞书 Feishu' },
  { id: 'telegram', label: 'Telegram' },
  { id: 'wecom', label: '企业微信 WeCom' },
  { id: 'discord', label: 'Discord' },
  { id: 'slack', label: 'Slack' },
  { id: 'signal', label: 'Signal' },
  { id: 'tui', label: 'TUI (终端)' },
];

type ModelOption = { id: string; l: string; p: string };

const STATUS_TEXT: Record<string, string> = {
  ok: '正常',
  timeout: '超时',
  failed: '失败',
  degraded: '降级',
  offline: '离线',
  unknown: '未知',
};

const statusClass = (status?: string) => {
  const s = status || 'unknown';
  if (['ok', 'timeout', 'failed', 'degraded', 'offline'].includes(s)) return s;
  return 'unknown';
};

const shortTime = (value?: string) => {
  if (!value) return '无记录';
  return value.substring(0, 16).replace('T', ' ');
};

const labelForModel = (modelId: string) => {
  const raw = modelId.split('/').pop() || modelId;
  return raw
    .replace(/[-_]/g, ' ')
    .replace(/\b\w/g, (m) => m.toUpperCase());
};

const providerForModel = (modelId: string) => {
  const provider = modelId.includes('/') ? modelId.split('/')[0] : '';
  const names: Record<string, string> = {
    opencode: 'OpenCode',
    'github-copilot': 'GitHub Copilot',
    copilot: 'Copilot',
    anthropic: 'Anthropic',
    openai: 'OpenAI',
    'openai-codex': 'OpenAI Codex',
    google: 'Google',
  };
  return names[provider] || provider || 'Custom';
};

const buildModelOptions = (agentConfig: AgentConfig) => {
  const options: ModelOption[] = agentConfig.knownModels?.length
    ? agentConfig.knownModels.map((m) => ({ id: m.id, l: m.label || labelForModel(m.id), p: m.provider || providerForModel(m.id) }))
    : [...FALLBACK_MODELS];
  const seen = new Set(options.map((m) => m.id));
  const ensure = (modelId?: string) => {
    if (!modelId || seen.has(modelId)) return;
    seen.add(modelId);
    options.unshift({ id: modelId, l: labelForModel(modelId), p: providerForModel(modelId) });
  };
  agentConfig.agents.forEach((ag) => ensure(ag.model));
  agentConfig.agents.forEach((ag) => ensure(ag.defaultModel));
  ensure(agentConfig.defaultModel);
  return options;
};

export default function ModelConfig() {
  const agentConfig = useStore((s) => s.agentConfig);
  const changeLog = useStore((s) => s.changeLog);
  const loadAgentConfig = useStore((s) => s.loadAgentConfig);
  const toast = useStore((s) => s.toast);

  const [selMap, setSelMap] = useState<Record<string, string>>({});
  const [statusMap, setStatusMap] = useState<Record<string, { cls: string; text: string }>>({});
  const [channelSel, setChannelSel] = useState('feishu');
  const [channelStatus, setChannelStatus] = useState('');
  const [health, setHealth] = useState<ModelHealthData | null>(null);
  const [healthError, setHealthError] = useState('');
  const [healthLoading, setHealthLoading] = useState(false);

  const loadHealth = async () => {
    setHealthLoading(true);
    try {
      const data = await api.modelHealth();
      setHealth(data);
      setHealthError('');
    } catch {
      setHealthError('模型健康接口不可达');
    } finally {
      setHealthLoading(false);
    }
  };

  useEffect(() => {
    loadAgentConfig();
    loadHealth();
  }, [loadAgentConfig]);

  useEffect(() => {
    if (agentConfig?.agents) {
      const m: Record<string, string> = {};
      agentConfig.agents.forEach((ag) => {
        m[ag.id] = ag.model;
      });
      setSelMap(m);
    }
    if (agentConfig?.dispatchChannel) {
      setChannelSel(agentConfig.dispatchChannel);
    }
  }, [agentConfig]);

  const healthByAgent = useMemo(() => {
    const map: Record<string, ModelHealthAgent> = {};
    health?.agents?.forEach((item) => {
      map[item.agentId] = item;
    });
    return map;
  }, [health]);
  const summary = health?.summary || {};
  const unhealthyCount = (summary.timeout || 0) + (summary.failed || 0) + (summary.degraded || 0) + (summary.offline || 0);

  if (!agentConfig?.agents) {
    return <div className="empty" style={{ gridColumn: '1/-1' }}>⚠️ 请先启动本地服务器</div>;
  }

  const models = buildModelOptions(agentConfig);

  const handleSelect = (agentId: string, val: string) => {
    setSelMap((p) => ({ ...p, [agentId]: val }));
  };

  const resetMC = (agentId: string) => {
    const ag = agentConfig.agents.find((a) => a.id === agentId);
    if (ag) setSelMap((p) => ({ ...p, [agentId]: ag.model }));
  };

  const applyModel = async (agentId: string) => {
    const model = selMap[agentId];
    if (!model) return;
    setStatusMap((p) => ({ ...p, [agentId]: { cls: 'pending', text: '⟳ 提交中…' } }));
    try {
      const r = await api.setModel(agentId, model);
      if (r.ok) {
        setStatusMap((p) => ({ ...p, [agentId]: { cls: 'ok', text: '✅ 已提交，运行时配置刷新中（约5秒）' } }));
        toast(agentId + ' 模型已更改', 'ok');
        setTimeout(() => { loadAgentConfig(); loadHealth(); }, 5500);
      } else {
        setStatusMap((p) => ({ ...p, [agentId]: { cls: 'err', text: '❌ ' + (r.error || '错误') } }));
      }
    } catch {
      setStatusMap((p) => ({ ...p, [agentId]: { cls: 'err', text: '❌ 无法连接服务器' } }));
    }
  };

  return (
    <div>
      <div className="model-health-panel">
        <div className="mh-head">
          <div>
            <div className="sec-title">模型连接状态</div>
            <div className="mh-sub">
              {health?.runtimeLabel || '运行时'} 观测面板 · 基于真实派发、超时和 session 错误回写
            </div>
          </div>
          <button className="btn btn-g" onClick={loadHealth} disabled={healthLoading}>
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

      <div className="model-grid">
        {agentConfig.agents.map((ag) => {
          const sel = selMap[ag.id] || ag.model;
          const changed = sel !== ag.model;
          const st = statusMap[ag.id];
          const h = healthByAgent[ag.id];
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
                <span>失败 {h?.failureCount || 0}</span>
                <span>超时 {h?.timeoutCount || 0}</span>
              </div>
              <select className="msel" value={sel} onChange={(e) => handleSelect(ag.id, e.target.value)}>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.l} ({m.p})
                  </option>
                ))}
              </select>
              <div className="mc-btns">
                <button className="btn btn-p" disabled={!changed} onClick={() => applyModel(ag.id)}>
                  应用
                </button>
                <button className="btn btn-g" onClick={() => resetMC(ag.id)}>
                  重置
                </button>
              </div>
              {st && <div className={`mc-st ${st.cls}`}>{st.text}</div>}
            </div>
          );
        })}
      </div>

      {/* Dispatch Channel 配置 */}
      <div style={{ marginTop: 24, marginBottom: 8 }}>
        <div className="sec-title">派发渠道</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 0' }}>
          <select className="msel" value={channelSel} onChange={(e) => setChannelSel(e.target.value)}
            style={{ maxWidth: 220 }}>
            {CHANNELS.map((ch) => (
              <option key={ch.id} value={ch.id}>{ch.label}</option>
            ))}
          </select>
          <button className="btn btn-p" disabled={channelSel === (agentConfig?.dispatchChannel || 'feishu')}
            onClick={async () => {
              try {
                const r = await api.setDispatchChannel(channelSel);
                if (r.ok) { setChannelStatus('✅ 已保存'); toast('派发渠道已切换', 'ok'); loadAgentConfig(); }
                else setChannelStatus('❌ ' + (r.error || '失败'));
              } catch { setChannelStatus('❌ 无法连接'); }
              setTimeout(() => setChannelStatus(''), 3000);
            }}>应用</button>
          {channelStatus && <span style={{ fontSize: 12, color: channelStatus.startsWith('✅') ? 'var(--success)' : 'var(--danger)' }}>{channelStatus}</span>}
        </div>
        <div style={{ fontSize: 11, color: 'var(--muted)' }}>自动派发时使用的通知渠道；OpenClaw 模式需在 openclaw.json 中配置对应 channel。</div>
      </div>

      {/* Change Log */}
      <div style={{ marginTop: 24 }}>
        <div className="sec-title">变更日志</div>
        <div className="cl-list">
          {!changeLog?.length ? (
            <div style={{ fontSize: 12, color: 'var(--muted)', padding: '8px 0' }}>暂无变更</div>
          ) : (
            [...changeLog]
              .reverse()
              .slice(0, 15)
              .map((e, i) => (
                <div className="cl-row" key={i}>
                  <span className="cl-t">{(e.at || '').substring(0, 16).replace('T', ' ')}</span>
                  <span className="cl-a">{e.agentId}</span>
                  <span className="cl-c">
                    <b>{e.oldModel}</b> → <b>{e.newModel}</b>
                    {e.rolledBack && (
                      <span
                        style={{
                          color: 'var(--danger)',
                          fontSize: 10,
                          border: '1px solid #ff527044',
                          padding: '1px 5px',
                          borderRadius: 3,
                          marginLeft: 4,
                        }}
                      >
                        ⚠ 已回滚
                      </span>
                    )}
                  </span>
                </div>
              ))
          )}
        </div>
      </div>
    </div>
  );
}
