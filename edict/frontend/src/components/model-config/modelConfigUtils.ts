import type { AgentConfig, ModelRegistryData, ModelRegistryEntry } from '../../api';

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
