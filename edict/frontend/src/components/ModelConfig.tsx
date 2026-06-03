import { type FormEvent, useEffect, useMemo, useState } from 'react';
import { useStore } from '../store';
import {
  api,
  type AgentConfig,
  type ModelHealthAgent,
  type ModelHealthData,
  type ModelProbeData,
  type ModelRegistryData,
  type ModelRegistryEntry,
} from '../api';

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

type ModelOption = { id: string; l: string; p: string; latencyMs?: number | null; status?: string; source?: string };

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
    'moonshotai-cn': 'Moonshot AI (China)',
    moonshot: 'Moonshot AI',
    anthropic: 'Anthropic',
    openai: 'OpenAI',
    'openai-codex': 'OpenAI Codex',
    google: 'Google',
  };
  return names[provider] || provider || 'Custom';
};

const sourceLabel = (source?: string) => {
  const labels: Record<string, string> = {
    'opencode-cli': 'CLI',
    'opencode-server': 'Server',
    'agent-config': 'Agent',
    'manual-api': 'Manual',
  };
  return labels[source || ''] || source || 'unknown';
};

const formatLatency = (value?: number | null) => {
  if (typeof value !== 'number' || Number.isNaN(value)) return '未观测';
  if (value < 1000) return `${Math.round(value)}ms`;
  return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)}s`;
};

const formatInterval = (value?: number) => {
  if (!value) return '未设置';
  if (value < 60) return `${value}s`;
  return `${Math.round(value / 60)}min`;
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

const buildRegistryOptions = (registry: ModelRegistryData | null, agentConfig: AgentConfig | null) => {
  const base: ModelOption[] = registry?.models?.length
    ? registry.models.map((m) => ({
      id: m.id,
      l: m.label || labelForModel(m.id),
      p: m.provider || providerForModel(m.id),
      latencyMs: m.latencyMs,
      status: m.recentStatus,
      source: m.source,
    }))
    : agentConfig
      ? buildModelOptions(agentConfig)
      : FALLBACK_MODELS;
  const seen = new Set<string>();
  const options: ModelOption[] = [];
  const add = (entry: ModelOption) => {
    if (!entry.id || seen.has(entry.id)) return;
    seen.add(entry.id);
    options.push(entry);
  };
  base.forEach(add);
  agentConfig?.agents?.forEach((ag) => add({ id: ag.model, l: labelForModel(ag.model), p: providerForModel(ag.model) }));
  agentConfig?.agents?.forEach((ag) => add({ id: ag.defaultModel || '', l: labelForModel(ag.defaultModel || ''), p: providerForModel(ag.defaultModel || '') }));
  if (agentConfig?.defaultModel) add({ id: agentConfig.defaultModel, l: labelForModel(agentConfig.defaultModel), p: providerForModel(agentConfig.defaultModel) });
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
  const [registry, setRegistry] = useState<ModelRegistryData | null>(null);
  const [registryError, setRegistryError] = useState('');
  const [registryLoading, setRegistryLoading] = useState(false);
  const [probe, setProbe] = useState<ModelProbeData | null>(null);
  const [probeError, setProbeError] = useState('');
  const [probeLoading, setProbeLoading] = useState('');
  const [modelQuery, setModelQuery] = useState('');
  const [customModel, setCustomModel] = useState({
    providerId: 'openrouter',
    providerName: 'OpenRouter',
    modelId: '',
    label: '',
    apiType: 'openai',
    baseURL: '',
    apiKey: '',
  });
  const [customStatus, setCustomStatus] = useState('');

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

  const loadRegistry = async (refresh = false) => {
    setRegistryLoading(true);
    try {
      const data = refresh ? await api.refreshModelRegistry() : await api.modelRegistry();
      setRegistry(data);
      setRegistryError(data.errors?.length ? data.errors.join('；') : '');
      if (refresh) {
        await loadAgentConfig();
        await loadHealth();
      }
    } catch {
      setRegistryError('模型注册表接口不可达');
    } finally {
      setRegistryLoading(false);
    }
  };

  const loadProbes = async () => {
    try {
      const data = await api.modelProbes();
      setProbe(data);
      setProbeError('');
    } catch {
      setProbeError('模型观测接口不可达');
    }
  };

  useEffect(() => {
    loadAgentConfig();
    loadHealth();
    loadRegistry(false);
    loadProbes();
  }, [loadAgentConfig]);

  useEffect(() => {
    const active = Boolean(probe?.running || probe?.config?.enabled);
    const timer = window.setInterval(async () => {
      await loadProbes();
      await loadRegistry(false);
      if (active) await loadHealth();
    }, active ? 4000 : 15000);
    return () => window.clearInterval(timer);
  }, [probe?.running, probe?.config?.enabled]);

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
  const models = useMemo(() => buildRegistryOptions(registry, agentConfig), [registry, agentConfig]);
  const registryById = useMemo(() => {
    const map: Record<string, ModelRegistryEntry> = {};
    registry?.models?.forEach((item) => {
      map[item.id] = item;
    });
    return map;
  }, [registry]);
  const visibleRegistryModels = useMemo(() => {
    const q = modelQuery.trim().toLowerCase();
    const items = [...(registry?.models || [])].filter((item) => {
      if (!q) return true;
      return [
        item.id,
        item.label,
        item.provider,
        item.providerId,
        item.tierLabel,
        ...(item.sources || []),
      ].some((value) => String(value || '').toLowerCase().includes(q));
    });
    return items.sort((a, b) => {
      const sourceRank = (item: ModelRegistryEntry) => {
        const sources = item.sources || [item.source || ''];
        if (sources.includes('opencode-cli')) return 0;
        if (sources.includes('opencode-server')) return 1;
        if (sources.includes('manual-api')) return 2;
        return 3;
      };
      const sr = sourceRank(a) - sourceRank(b);
      if (sr !== 0) return sr;
      const al = typeof a.latencyMs === 'number' ? a.latencyMs : Number.MAX_SAFE_INTEGER;
      const bl = typeof b.latencyMs === 'number' ? b.latencyMs : Number.MAX_SAFE_INTEGER;
      if (al !== bl) return al - bl;
      return `${a.provider || ''}${a.label || a.id}`.localeCompare(`${b.provider || ''}${b.label || b.id}`);
    });
  }, [registry, modelQuery]);

  const probeSummary = probe?.summary;
  const probeRunning = Boolean(probe?.running);
  const probeEnabled = Boolean(probe?.config?.enabled);
  const probeShouldStop = probeRunning || probeEnabled;
  const probeQueueCount = probe?.queue?.length || 0;

  if (!agentConfig?.agents) {
    return <div className="empty" style={{ gridColumn: '1/-1' }}>⚠️ 请先启动本地服务器</div>;
  }

  const handleSelect = (agentId: string, val: string) => {
    setSelMap((p) => ({ ...p, [agentId]: val }));
  };

  const refreshRegistry = async () => {
    await loadRegistry(true);
    await loadProbes();
    toast('OpenCode 模型已同步', 'ok');
  };

  const runProbe = async (mode: 'all' | 'visible') => {
    const modelIds = mode === 'visible' ? visibleRegistryModels.map((m) => m.id) : [];
    setProbeLoading(mode);
    try {
      const result = await api.runModelProbes({ modelIds, timeoutSec: 25 });
      if (!result.ok) {
        setProbeError(result.error || '无法启动模型观测');
        return;
      }
      if (result.probes) setProbe(result.probes);
      await loadRegistry(false);
      toast(result.started === false ? '模型观测已在运行' : `已开始观测 ${result.count || modelIds.length || registry?.summary?.total || models.length} 个模型`, 'ok');
    } catch {
      setProbeError('无法启动模型观测');
    } finally {
      setProbeLoading('');
    }
  };

  const toggleContinuousProbe = async () => {
    setProbeLoading('continuous');
    try {
      const result = probeShouldStop
        ? await api.stopModelProbes()
        : await api.startModelProbes({
          modelIds: modelQuery.trim() ? visibleRegistryModels.map((m) => m.id) : [],
          intervalSec: 300,
          timeoutSec: 25,
        });
      if (!result.ok) {
        setProbeError(result.error || '无法切换持续观测');
        return;
      }
      if (result.probes) setProbe(result.probes);
      await loadRegistry(false);
      toast(probeShouldStop ? '模型观测已停止' : '持续观测已开启', 'ok');
    } catch {
      setProbeError('无法切换持续观测');
    } finally {
      setProbeLoading('');
    }
  };

  const updateCustom = (key: keyof typeof customModel, value: string) => {
    setCustomModel((prev) => ({ ...prev, [key]: value }));
  };

  const submitCustom = async (event: FormEvent) => {
    event.preventDefault();
    setCustomStatus('保存中');
    try {
      const result = await api.addCustomModel(customModel);
      if (result.ok) {
        if (result.registry) setRegistry(result.registry);
        setCustomStatus(result.restartRequired ? '已保存，重启 OpenCode 后生效' : '已保存');
        setCustomModel((prev) => ({ ...prev, modelId: '', label: '', apiKey: '' }));
        toast('手动 API 模型已加入列表', 'ok');
        await loadAgentConfig();
      } else {
        setCustomStatus(result.error || '保存失败');
      }
    } catch {
      setCustomStatus('无法连接服务器');
    }
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
        setStatusMap((p) => ({ ...p, [agentId]: { cls: 'ok', text: `已切换到 ${model}` } }));
        toast(agentId + ' 模型已更改', 'ok');
        await loadAgentConfig();
        await loadRegistry(false);
        await loadHealth();
      } else {
        setStatusMap((p) => ({ ...p, [agentId]: { cls: 'err', text: r.error || '模型切换失败' } }));
      }
    } catch {
      setStatusMap((p) => ({ ...p, [agentId]: { cls: 'err', text: '无法连接服务器' } }));
    }
  };

  return (
    <div>
      <div className="model-registry-panel">
        <div className="mr-head">
          <div>
            <div className="sec-title">模型注册表</div>
            <div className="mr-sub">
              OpenCode 实时模型 · 手动 API · 延迟观测统一入口
            </div>
          </div>
          <button className="btn btn-p mr-refresh" onClick={refreshRegistry} disabled={registryLoading}>
            {registryLoading ? '同步中' : '同步 OpenCode'}
          </button>
        </div>

        <div className="mr-kpis">
          <div className="mr-kpi">
            <span>可选模型</span>
            <b>{registry?.summary?.total || models.length}</b>
          </div>
          <div className="mr-kpi">
            <span>已观测延迟</span>
            <b>{registry?.summary?.measured || 0}</b>
          </div>
          <div className="mr-kpi">
            <span>Provider</span>
            <b>{Object.keys(registry?.summary?.providers || {}).length}</b>
          </div>
          <div className="mr-kpi">
            <span>最近同步</span>
            <b>{registry?.generatedAt ? shortTime(registry.generatedAt) : '未同步'}</b>
          </div>
        </div>

        <div className="mr-sources">
          {(registry?.sources || []).map((src) => (
            <div className={`mr-source ${src.ok ? 'ok' : 'bad'}`} key={src.id}>
              <span>{src.label}</span>
              <b>{src.count}</b>
              <small>{formatLatency(src.latencyMs)}{src.error ? ` · ${src.error}` : ''}</small>
            </div>
          ))}
        </div>

        <div className="mr-probe-strip">
          <div className={`mr-probe-state ${probeRunning ? 'running' : probeEnabled ? 'enabled' : ''}`}>
            <span>{probeRunning ? '正在观测' : probeEnabled ? '持续观测已开' : '观测已停止'}</span>
            <b>{probe?.currentModel ? labelForModel(probe.currentModel) : probeQueueCount ? `${probeQueueCount} 个排队` : '空闲'}</b>
            <small>间隔 {formatInterval(probe?.config?.intervalSec)} · 已测 {probeSummary?.measured || registry?.summary?.measured || 0}</small>
          </div>
          <button className="btn btn-g" onClick={() => runProbe('visible')} disabled={!!probeLoading || !visibleRegistryModels.length}>
            {probeLoading === 'visible' ? '启动中' : '观测当前列表'}
          </button>
          <button className="btn btn-g" onClick={() => runProbe('all')} disabled={!!probeLoading || !(registry?.summary?.total || models.length)}>
            {probeLoading === 'all' ? '启动中' : '观测全部'}
          </button>
          <button className={probeShouldStop ? 'btn btn-g' : 'btn btn-p'} onClick={toggleContinuousProbe} disabled={!!probeLoading}>
            {probeLoading === 'continuous' ? '切换中' : probeShouldStop ? '停止观测' : '开启持续观测'}
          </button>
        </div>

        {(registryError || probeError) && <div className="mh-error">{registryError || probeError}</div>}

        <div className="mr-tools">
          <input
            value={modelQuery}
            onChange={(e) => setModelQuery(e.target.value)}
            placeholder="搜索模型 / provider，例如 kimi、gpt-5.5、openai"
          />
          <span>当前显示 {visibleRegistryModels.length} / {registry?.summary?.total || models.length}</span>
        </div>

        <div className="mr-layout">
          <div className="mr-table">
            <div className="mr-row mr-row-head">
              <span>模型</span>
              <span>来源</span>
              <span>延迟</span>
              <span>状态</span>
            </div>
            {!visibleRegistryModels.length ? (
              <div className="mr-empty">暂无模型，点击同步 OpenCode</div>
            ) : (
              visibleRegistryModels.map((item) => {
                const cls = statusClass(item.recentStatus);
                return (
                  <div className="mr-row" key={item.id}>
                    <div className="mr-model">
                      <b>{item.label || labelForModel(item.id)}</b>
                      <small>{item.id}</small>
                    </div>
                    <div className="mr-source-tags">
                      {(item.sources?.length ? item.sources : [item.source || 'unknown']).map((src) => (
                        <span key={src}>{sourceLabel(src)}</span>
                      ))}
                    </div>
                    <div className="mr-latency">
                      <b>{formatLatency(item.latencyMs)}</b>
                      <small>{item.latencyLabel || '未观测'}{item.lastMeasuredAt ? ` · ${shortTime(item.lastMeasuredAt)}` : ''}</small>
                    </div>
                    <div className="mr-status-cell">
                      <span className={`mh-pill mini ${cls}`}>{item.statusLabel || STATUS_TEXT.unknown}</span>
                      <small>{item.provider || providerForModel(item.id)} · {item.tierLabel || '未知等级'}</small>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          <form className="mr-custom" onSubmit={submitCustom}>
            <div className="mr-custom-title">手动 API 模型</div>
            <div className="mr-form-grid">
              <label>
                <span>Provider ID</span>
                <input value={customModel.providerId} onChange={(e) => updateCustom('providerId', e.target.value)} placeholder="openrouter" />
              </label>
              <label>
                <span>Provider 名称</span>
                <input value={customModel.providerName} onChange={(e) => updateCustom('providerName', e.target.value)} placeholder="OpenRouter" />
              </label>
              <label className="wide">
                <span>模型 ID</span>
                <input value={customModel.modelId} onChange={(e) => updateCustom('modelId', e.target.value)} placeholder="anthropic/claude-3.5-sonnet 或 gpt-4.1" />
              </label>
              <label>
                <span>API 类型</span>
                <select value={customModel.apiType} onChange={(e) => updateCustom('apiType', e.target.value)}>
                  <option value="openai">OpenAI Compatible</option>
                  <option value="anthropic">Anthropic</option>
                  <option value="google">Google</option>
                  <option value="custom">Custom</option>
                </select>
              </label>
              <label>
                <span>显示名</span>
                <input value={customModel.label} onChange={(e) => updateCustom('label', e.target.value)} placeholder="可留空" />
              </label>
              <label className="wide">
                <span>Base URL</span>
                <input value={customModel.baseURL} onChange={(e) => updateCustom('baseURL', e.target.value)} placeholder="https://api.openrouter.ai/v1" />
              </label>
              <label className="wide">
                <span>API Key</span>
                <input type="password" value={customModel.apiKey} onChange={(e) => updateCustom('apiKey', e.target.value)} placeholder="留空则保留已有密钥" />
              </label>
            </div>
            <div className="mr-custom-actions">
              <button className="btn btn-p" type="submit" disabled={!customModel.modelId.trim()}>
                保存模型
              </button>
              {customStatus && <span>{customStatus}</span>}
            </div>
          </form>
        </div>
      </div>

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
              <select className="msel" value={sel} onChange={(e) => handleSelect(ag.id, e.target.value)}>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.l} ({m.p}) · {formatLatency(m.latencyMs)}
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
