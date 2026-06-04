import type { FormEvent } from 'react';
import type { ModelProbeData, ModelRegistryData, ModelRegistryEntry } from '../../api';
import { ManualApiModelForm, type CustomModelDraft } from './ManualApiModelForm';
import {
  formatInterval,
  formatLatency,
  labelForModel,
  providerForModel,
  shortTime,
  sourceLabel,
  statusClass,
  STATUS_TEXT,
  type ModelOption,
} from './modelConfigUtils';

type ModelRegistryPanelProps = {
  registry: ModelRegistryData | null;
  registryLoading: boolean;
  registryError: string;
  probe: ModelProbeData | null;
  probeError: string;
  probeLoading: string;
  modelQuery: string;
  models: ModelOption[];
  visibleRegistryModels: ModelRegistryEntry[];
  customModel: CustomModelDraft;
  customStatus: string;
  onRefreshRegistry: () => void;
  onRunProbe: (mode: 'all' | 'visible') => void;
  onToggleContinuousProbe: () => void;
  onModelQueryChange: (value: string) => void;
  onCustomChange: (key: keyof CustomModelDraft, value: string) => void;
  onCustomSubmit: (event: FormEvent) => void;
};

export function ModelRegistryPanel({
  registry,
  registryLoading,
  registryError,
  probe,
  probeError,
  probeLoading,
  modelQuery,
  models,
  visibleRegistryModels,
  customModel,
  customStatus,
  onRefreshRegistry,
  onRunProbe,
  onToggleContinuousProbe,
  onModelQueryChange,
  onCustomChange,
  onCustomSubmit,
}: ModelRegistryPanelProps) {
  const probeSummary = probe?.summary;
  const probeRunning = Boolean(probe?.running);
  const probeEnabled = Boolean(probe?.config?.enabled);
  const observerRunning = Boolean(probe?.observerRunning);
  const probeShouldStop = probeRunning || probeEnabled;
  const probeQueueCount = probe?.queue?.length || 0;

  return (
    <div className="model-registry-panel">
      <div className="mr-head">
        <div>
          <div className="sec-title">模型注册表</div>
          <div className="mr-sub">
            OpenCode 实时模型 · 手动 API · 延迟观测统一入口
          </div>
        </div>
        <button className="btn btn-p mr-refresh" onClick={onRefreshRegistry} disabled={registryLoading}>
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
          <span>{probeRunning ? '正在观测' : observerRunning ? '后台待命' : probeEnabled ? '持续观测已开' : '观测已停止'}</span>
          <b>{probe?.currentModel ? labelForModel(probe.currentModel) : probeQueueCount ? `${probeQueueCount} 个排队` : '空闲'}</b>
          <small>间隔 {formatInterval(probe?.config?.intervalSec)} · 已测 {probeSummary?.measured || registry?.summary?.measured || 0}</small>
        </div>
        <button className="btn btn-g" onClick={() => onRunProbe('visible')} disabled={!!probeLoading || !visibleRegistryModels.length}>
          {probeLoading === 'visible' ? '启动中' : '观测当前列表'}
        </button>
        <button className="btn btn-g" onClick={() => onRunProbe('all')} disabled={!!probeLoading || !(registry?.summary?.total || models.length)}>
          {probeLoading === 'all' ? '启动中' : '观测全部'}
        </button>
        <button className={probeShouldStop ? 'btn btn-g' : 'btn btn-p'} onClick={onToggleContinuousProbe} disabled={!!probeLoading}>
          {probeLoading === 'continuous' ? '切换中' : probeShouldStop ? '停止观测' : '开启持续观测'}
        </button>
      </div>

      {(registryError || probeError) && <div className="mh-error">{registryError || probeError}</div>}

      <div className="mr-tools">
        <input
          value={modelQuery}
          onChange={(e) => onModelQueryChange(e.target.value)}
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

        <ManualApiModelForm
          customModel={customModel}
          customStatus={customStatus}
          onChange={onCustomChange}
          onSubmit={onCustomSubmit}
        />
      </div>
    </div>
  );
}
