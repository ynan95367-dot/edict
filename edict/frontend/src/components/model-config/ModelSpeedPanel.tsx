import { Activity, CheckCircle2, Gauge, RadioTower, RefreshCw, Search, Zap } from 'lucide-react';
import { useMemo } from 'react';
import type { AgentConfig, ModelHealthData, ModelProbeData, ModelRegistryData, ModelRegistryEntry } from '../../api';
import {
  buildModelSpeedNodes,
  formatInterval,
  formatLatency,
  labelForModel,
  pickRecommendedNode,
  sourceLabel,
  type ModelSpeedNode,
  type ModelSpeedTone,
} from './modelConfigUtils';

const NODE_RENDER_LIMIT = 80;

type ModelSpeedPanelProps = {
  agentConfig: AgentConfig;
  registry: ModelRegistryData | null;
  health: ModelHealthData | null;
  probe: ModelProbeData | null;
  registryLoading: boolean;
  registryError: string;
  probeError: string;
  probeLoading: string;
  modelQuery: string;
  visibleRegistryModels: ModelRegistryEntry[];
  onRefreshRegistry: () => void;
  onRunProbe: (mode: 'all' | 'visible') => void;
  onToggleContinuousProbe: () => void;
  onModelQueryChange: (value: string) => void;
};

const toneText: Record<ModelSpeedTone, string> = {
  ok: '可用',
  slow: '偏慢',
  err: 'Error',
  idle: '未测',
  running: '测速中',
};

function nodeSubtitle(node: ModelSpeedNode) {
  const parts = [
    node.provider,
    node.source ? sourceLabel(node.source) : '',
    node.lastMeasuredAt ? node.lastMeasuredAt.replace('T', ' ').slice(0, 16) : '',
  ].filter(Boolean);
  return parts.join(' · ') || '等待测速';
}

export function ModelSpeedPanel({
  agentConfig,
  registry,
  health,
  probe,
  registryLoading,
  registryError,
  probeError,
  probeLoading,
  modelQuery,
  visibleRegistryModels,
  onRefreshRegistry,
  onRunProbe,
  onToggleContinuousProbe,
  onModelQueryChange,
}: ModelSpeedPanelProps) {
  const nodes = useMemo(() => buildModelSpeedNodes({
    agentConfig,
    registry,
    health,
    probe,
    models: visibleRegistryModels,
  }), [agentConfig, registry, health, probe, visibleRegistryModels]);

  const recommended = useMemo(() => pickRecommendedNode(nodes), [nodes]);
  const currentNodes = nodes.filter((node) => node.isCurrent).slice(0, 6);
  const shownNodes = nodes.slice(0, NODE_RENDER_LIMIT);
  const hiddenCount = Math.max(0, nodes.length - shownNodes.length);
  const running = Boolean(probe?.running);
  const continuous = Boolean(probe?.config?.enabled);
  const probeQueueCount = probe?.queue?.length || 0;
  const summary = health?.summary || {};
  const availableCount = nodes.filter((node) => node.tone === 'ok' || node.tone === 'slow').length;
  const errorCount = nodes.filter((node) => node.tone === 'err').length;
  const untestedCount = nodes.filter((node) => node.tone === 'idle').length;
  const runtimeOk = health?.gateway?.alive && health.gateway.probe;
  const errors = [registryError, probeError].filter(Boolean).join('；');

  return (
    <section className="model-speed-panel">
      <div className="msp-head">
        <div>
          <span className="msp-kicker">模型实时检测</span>
          <h2>{runtimeOk ? 'OpenCode 可用' : 'OpenCode 需要检查'}</h2>
          <p>
            {running
              ? `正在检测 ${probe?.currentModel ? labelForModel(probe.currentModel) : '模型'}，队列 ${probeQueueCount} 个`
              : continuous
                ? `持续检测已开启，间隔 ${formatInterval(probe?.config?.intervalSec)}`
                : '默认只检测当前 Agent 正在使用的模型；需要时再检测筛选结果。'}
          </p>
        </div>
        <div className={`msp-runtime ${runtimeOk ? 'ok' : 'err'}`}>
          <RadioTower size={18} />
          <span>{health?.runtimeLabel || registry?.runtimeLabel || 'OpenCode'}</span>
          <b>{runtimeOk ? '在线' : '异常'}</b>
        </div>
      </div>

      <div className="msp-toolbar">
        <label className="msp-search">
          <Search size={15} />
          <input
            value={modelQuery}
            onChange={(event) => onModelQueryChange(event.target.value)}
            placeholder="搜索模型、Provider 或地区关键词"
          />
        </label>
        <button className="btn btn-g" onClick={() => onRunProbe('all')} disabled={!!probeLoading}>
          <Gauge size={14} />{probeLoading === 'all' ? '启动中' : '测当前模型'}
        </button>
        <button className="btn btn-g" onClick={() => onRunProbe('visible')} disabled={!!probeLoading || !visibleRegistryModels.length}>
          <Activity size={14} />{probeLoading === 'visible' ? '启动中' : '测筛选结果'}
        </button>
        <button className={continuous || running ? 'btn btn-g active' : 'btn btn-g'} onClick={onToggleContinuousProbe} disabled={!!probeLoading}>
          <Zap size={14} />{probeLoading === 'continuous' ? '切换中' : running ? '停止检测' : continuous ? '停止持续检测' : '持续检测'}
        </button>
        <button className="btn btn-g icon-only" onClick={onRefreshRegistry} disabled={registryLoading} title="同步 OpenCode 模型">
          <RefreshCw size={15} />
        </button>
      </div>

      {errors && <div className="msp-error">{errors}</div>}

      <div className="msp-summary">
        <div className="msp-recommend">
          <span>当前推荐</span>
          <b>{recommended?.label || '等待测速'}</b>
          <small>{recommended ? `${recommended.provider} · ${recommended.latencyText}` : '先检测当前模型'}</small>
        </div>
        <div><span>当前 Agent</span><b>{currentNodes.length || agentConfig.agents.length}</b><small>正在使用的模型组</small></div>
        <div><span>可用节点</span><b>{availableCount}</b><small>绿色/黄色可执行</small></div>
        <div><span>错误节点</span><b>{errorCount}</b><small>超时、失败或离线</small></div>
        <div><span>未测速</span><b>{untestedCount}</b><small>需要手动检测</small></div>
      </div>

      <div className="msp-current-strip">
        {currentNodes.length ? currentNodes.map((node) => (
          <div className={`msp-current-node ${node.tone}`} key={node.id}>
            <CheckCircle2 size={15} />
            <span>{node.currentAgentLabels.join(' / ')}</span>
            <b>{node.label}</b>
            <em>{node.latencyText}</em>
          </div>
        )) : (
          <div className="msp-current-empty">当前没有可识别的 Agent 模型。</div>
        )}
      </div>

      <div className="msp-node-list">
        {shownNodes.map((node) => (
          <div className={`msp-node ${node.tone}${node.isCurrent ? ' current' : ''}${node.isRecommended ? ' recommended' : ''}`} key={node.id}>
            <div className="msp-node-main">
              <b>{node.label}</b>
              <small>{node.id}</small>
            </div>
            <div className="msp-node-meta">
              <span>{nodeSubtitle(node)}</span>
              {node.isCurrent && <em>{node.currentAgentLabels.join(' / ')}</em>}
            </div>
            <div className={`msp-latency ${node.tone}`}>{node.latencyText}</div>
            <div className={`msp-status ${node.tone}`}>{toneText[node.tone]}</div>
          </div>
        ))}
        {!shownNodes.length && (
          <div className="msp-empty">没有匹配模型。换个关键词，或先同步 OpenCode。</div>
        )}
      </div>

      {hiddenCount > 0 && (
        <div className="msp-more">为保持页面流畅，当前只渲染前 {NODE_RENDER_LIMIT} 个节点；继续搜索可缩小范围。还有 {hiddenCount} 个未显示。</div>
      )}

      <div className="msp-foot">
        <span>OpenCode 当前健康：正常 {summary.ok || 0} · 异常 {(summary.timeout || 0) + (summary.failed || 0) + (summary.degraded || 0) + (summary.offline || 0)} · 暂无观测 {summary.unknown || 0}</span>
        <span>模型列表 {visibleRegistryModels.length} / {registry?.summary?.total || nodes.length}</span>
      </div>
    </section>
  );
}
