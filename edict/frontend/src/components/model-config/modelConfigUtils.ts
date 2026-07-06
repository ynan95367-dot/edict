import type { AgentConfig, ModelHealthData, ModelProbeData, ModelRegistryData, ModelRegistryEntry } from '../../api';

export const FALLBACK_MODELS = [
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

export const CHANNELS = [
  { id: 'feishu', label: '飞书 Feishu' },
  { id: 'telegram', label: 'Telegram' },
  { id: 'wecom', label: '企业微信 WeCom' },
  { id: 'discord', label: 'Discord' },
  { id: 'slack', label: 'Slack' },
  { id: 'signal', label: 'Signal' },
  { id: 'tui', label: 'TUI (终端)' },
];

export type ModelOption = {
  id: string;
  l: string;
  p: string;
  latencyMs?: number | null;
  status?: string;
  source?: string;
};

export type ModelSpeedTone = 'ok' | 'slow' | 'err' | 'idle' | 'running';

export type ModelSpeedNode = {
  id: string;
  label: string;
  provider: string;
  status: string;
  statusLabel: string;
  tone: ModelSpeedTone;
  latencyMs?: number | null;
  latencyText: string;
  source?: string;
  lastMeasuredAt?: string;
  lastError?: string;
  currentAgentIds: string[];
  currentAgentLabels: string[];
  isCurrent: boolean;
  isRecommended: boolean;
};

export const STATUS_TEXT: Record<string, string> = {
  ok: '正常',
  timeout: '超时',
  failed: '失败',
  degraded: '降级',
  offline: '离线',
  unknown: '未知',
};

export const statusClass = (status?: string) => {
  const s = status || 'unknown';
  if (['ok', 'timeout', 'failed', 'degraded', 'offline'].includes(s)) return s;
  return 'unknown';
};

export const shortTime = (value?: string) => {
  if (!value) return '无记录';
  return value.substring(0, 16).replace('T', ' ');
};

export const labelForModel = (modelId: string) => {
  const raw = modelId.split('/').pop() || modelId;
  return raw
    .replace(/[-_]/g, ' ')
    .replace(/\b\w/g, (m) => m.toUpperCase());
};

export const providerForModel = (modelId: string) => {
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

export const sourceLabel = (source?: string) => {
  const labels: Record<string, string> = {
    'opencode-cli': 'CLI',
    'opencode-server': 'Server',
    'agent-config': 'Agent',
    'manual-api': 'Manual',
  };
  return labels[source || ''] || source || 'unknown';
};

export const formatLatency = (value?: number | null) => {
  if (typeof value !== 'number' || Number.isNaN(value)) return '未观测';
  if (value < 1000) return `${Math.round(value)}ms`;
  return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)}s`;
};

export const formatInterval = (value?: number) => {
  if (!value) return '未设置';
  if (value < 60) return `${value}s`;
  return `${Math.round(value / 60)}min`;
};

export const modelSpeedTone = (status?: string, latencyMs?: number | null, running = false): ModelSpeedTone => {
  if (running) return 'running';
  const normalized = status || 'unknown';
  if (['timeout', 'failed', 'degraded', 'offline', 'error'].includes(normalized)) return 'err';
  if (normalized === 'ok') {
    if (typeof latencyMs === 'number' && latencyMs >= 10000) return 'slow';
    return 'ok';
  }
  if (typeof latencyMs === 'number') return latencyMs >= 10000 ? 'slow' : 'ok';
  return 'idle';
};

export const speedLatencyLabel = (node: { tone?: ModelSpeedTone; latencyMs?: number | null }) => {
  if (node.tone === 'running') return '测速中';
  if (node.tone === 'err') return 'Error';
  if (node.tone === 'idle') return '-';
  return formatLatency(node.latencyMs);
};

export const buildModelOptions = (agentConfig: AgentConfig) => {
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

export const buildRegistryOptions = (registry: ModelRegistryData | null, agentConfig: AgentConfig | null) => {
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

export const sortRegistryModels = (items: ModelRegistryEntry[]) => {
  return [...items].sort((a, b) => {
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
};

export const buildCurrentModelIds = (agentConfig: AgentConfig | null) => {
  const seen = new Set<string>();
  const ids: string[] = [];
  agentConfig?.agents?.forEach((agent) => {
    const id = (agent.model || agent.defaultModel || '').trim();
    if (!id || seen.has(id)) return;
    seen.add(id);
    ids.push(id);
  });
  return ids;
};

export const pickRecommendedNode = (nodes: ModelSpeedNode[]) => {
  return nodes.find((node) => node.tone === 'ok' && typeof node.latencyMs === 'number')
    || nodes.find((node) => node.tone === 'ok')
    || nodes.find((node) => node.tone === 'slow')
    || nodes.find((node) => node.isCurrent)
    || nodes[0]
    || null;
};

export const buildModelSpeedNodes = ({
  agentConfig,
  registry,
  health,
  probe,
  models,
}: {
  agentConfig: AgentConfig | null;
  registry: ModelRegistryData | null;
  health: ModelHealthData | null;
  probe: ModelProbeData | null;
  models?: ModelRegistryEntry[];
}) => {
  const registryModels = models?.length ? models : (registry?.models || []);
  const currentByModel = new Map<string, { ids: string[]; labels: string[] }>();
  agentConfig?.agents?.forEach((agent) => {
    const modelId = (agent.model || agent.defaultModel || '').trim();
    if (!modelId) return;
    const entry = currentByModel.get(modelId) || { ids: [], labels: [] };
    entry.ids.push(agent.id);
    entry.labels.push(agent.label || agent.id);
    currentByModel.set(modelId, entry);
  });

  const probeRecords = new Map<string, NonNullable<ModelProbeData['records']>[string]>();
  Object.values(probe?.records || {}).forEach((record) => {
    if (record.model) probeRecords.set(record.model, record);
  });

  const healthByModel = new Map<string, NonNullable<ModelHealthData['agents']>[number]>();
  health?.agents?.forEach((agent) => {
    if (!agent.model) return;
    const current = healthByModel.get(agent.model);
    if (!current || statusClass(agent.status) === 'ok') {
      healthByModel.set(agent.model, agent);
    }
  });

  const seen = new Set<string>();
  const entries: ModelRegistryEntry[] = [];
  const addRegistry = (entry: ModelRegistryEntry) => {
    if (!entry.id || seen.has(entry.id)) return;
    seen.add(entry.id);
    entries.push(entry);
  };
  registryModels.forEach(addRegistry);
  currentByModel.forEach((_value, modelId) => {
    if (!seen.has(modelId)) {
      addRegistry({
        id: modelId,
        label: labelForModel(modelId),
        provider: providerForModel(modelId),
        recentStatus: healthByModel.get(modelId)?.status || 'unknown',
      });
    }
  });

  const nodes = entries.map((entry) => {
    const probeRecord = probeRecords.get(entry.id);
    const healthRecord = healthByModel.get(entry.id);
    const current = currentByModel.get(entry.id);
    const running = probe?.running && probe.currentModel === entry.id;
    const latencyMs = entry.latencyMs ?? probeRecord?.latencyMs ?? healthRecord?.lastLatencyMs ?? null;
    const status = entry.recentStatus || probeRecord?.status || healthRecord?.status || 'unknown';
    const tone = modelSpeedTone(status, latencyMs, running);
    const node: ModelSpeedNode = {
      id: entry.id,
      label: entry.label || labelForModel(entry.id),
      provider: entry.provider || providerForModel(entry.id),
      status,
      statusLabel: running ? '测速中' : entry.statusLabel || probeRecord?.statusLabel || healthRecord?.statusLabel || STATUS_TEXT[status] || STATUS_TEXT.unknown,
      tone,
      latencyMs,
      latencyText: '',
      source: entry.latencySource || entry.source || probeRecord?.source || healthRecord?.source,
      lastMeasuredAt: entry.lastMeasuredAt || probeRecord?.updatedAt || healthRecord?.lastSuccessAt || healthRecord?.lastFailureAt,
      lastError: entry.lastError || probeRecord?.lastError || healthRecord?.lastError || healthRecord?.historicalLastError,
      currentAgentIds: current?.ids || [],
      currentAgentLabels: current?.labels || [],
      isCurrent: Boolean(current),
      isRecommended: false,
    };
    node.latencyText = speedLatencyLabel(node);
    return node;
  });

  const toneRank: Record<ModelSpeedTone, number> = { ok: 0, slow: 1, running: 2, idle: 3, err: 4 };
  const compareBySpeed = (a: ModelSpeedNode, b: ModelSpeedNode) => {
    const toneDiff = toneRank[a.tone] - toneRank[b.tone];
    if (toneDiff !== 0) return toneDiff;
    const aLatency = typeof a.latencyMs === 'number' ? a.latencyMs : Number.MAX_SAFE_INTEGER;
    const bLatency = typeof b.latencyMs === 'number' ? b.latencyMs : Number.MAX_SAFE_INTEGER;
    if (aLatency !== bLatency) return aLatency - bLatency;
    return `${a.provider}${a.label}`.localeCompare(`${b.provider}${b.label}`);
  };

  const ordered = nodes.sort((a, b) => {
    const currentRank = Number(b.isCurrent) - Number(a.isCurrent);
    if (currentRank !== 0) return currentRank;
    return compareBySpeed(a, b);
  });
  const recommended = pickRecommendedNode([...ordered].sort(compareBySpeed));
  if (recommended) {
    const target = ordered.find((node) => node.id === recommended.id);
    if (target) target.isRecommended = true;
  }
  return ordered;
};
