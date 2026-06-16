import {
  Activity,
  AlertTriangle,
  Archive,
  CheckCircle2,
  Clock,
  Compass,
  FolderArchive,
  RotateCcw,
} from 'lucide-react';
import type { RuntimeOutboxHealth, RuntimeOutboxItem, RuntimeOutboxLayer } from '../../api';

type WorkerHealthPanelProps = {
  data: RuntimeOutboxHealth | null;
  onScan: () => void;
  onOpenTask: (taskId: string) => void;
  onRetry: (itemId: string) => void;
  onArchive: (itemId: string) => void;
  onArchiveAll: () => void;
};

function toneClass(tone?: string): 'ok' | 'warn' | 'err' | 'idle' {
  if (tone === 'err') return 'err';
  if (tone === 'warn') return 'warn';
  if (tone === 'idle') return 'idle';
  return 'ok';
}

function itemTitle(item: RuntimeOutboxItem): string {
  return item.taskTitle || item.trigger || item.kind || item.id;
}

function blockingLayerLabel(layer?: string): string {
  if (layer === 'model') return '模型';
  if (layer === 'runtime') return '运行时';
  if (layer === 'workspace') return '工作区';
  if (layer === 'history') return '历史';
  if (layer === 'queue') return '队列';
  return layer || '';
}

function QueueItem({
  item,
  tone,
  onOpenTask,
  onRetry,
  onArchive,
}: {
  item: RuntimeOutboxItem;
  tone: 'active' | 'failed';
  onOpenTask: (taskId: string) => void;
  onRetry?: (itemId: string) => void;
  onArchive?: (itemId: string) => void;
}) {
  const canOpen = !!item.taskId;
  return (
    <div className={`wh-item ${tone}`}>
      <button
        className="wh-item-main"
        type="button"
        disabled={!canOpen}
        onClick={() => item.taskId && onOpenTask(item.taskId)}
      >
        <span className="wh-item-id">{item.taskId || item.id}</span>
        <b>{itemTitle(item)}</b>
        <em>
          {item.kind || 'outbox'} · {item.agentId || '未指定'} · {item.status || 'unknown'}
          {item.ageText ? ` · ${item.ageText}` : ''}
        </em>
      </button>
      {tone === 'failed' ? (
        <>
          <span className="wh-error">{item.lastError || '无错误详情'}</span>
          <div className="wh-item-actions">
            {onRetry && (
              <button type="button" className="wh-action retry" onClick={() => onRetry(item.id)}>
                <RotateCcw size={13} />重试
              </button>
            )}
            {onArchive && (
              <button type="button" className="wh-action archive" onClick={() => onArchive(item.id)}>
                <Archive size={13} />归档
              </button>
            )}
          </div>
        </>
      ) : (
        <span className="wh-error">{item.trigger || item.traceId || '等待 worker 处理'}</span>
      )}
    </div>
  );
}

export function WorkerHealthPanel({
  data,
  onScan,
  onOpenTask,
  onRetry,
  onArchive,
  onArchiveAll,
}: WorkerHealthPanelProps) {
  const summary = data?.summary;
  const tone = toneClass(summary?.tone);
  const worker = data?.worker;
  const heartbeat = worker?.heartbeatAgeText
    ? `${worker.heartbeatAgeText}前`
    : worker?.active
      ? '读取中'
      : '未启动';
  const activeItems = data?.activeItems || [];
  const deadLetters = data?.deadLetters || [];
  const deadWindow = data?.deadLetterWindow;
  const deadReturned = deadWindow?.returned || deadLetters.length;
  const deadTotal = deadWindow?.total || data?.failed || 0;
  const visibleDeadLetters = deadLetters.slice(0, 4);
  const deadDisplayed = Math.min(visibleDeadLetters.length, deadReturned);
  const deadClipped = Boolean(deadWindow?.truncated || deadLetters.length > visibleDeadLetters.length);
  const layers = data?.layers || {};
  const visibleLayers: RuntimeOutboxLayer[] = ['current', 'ghost', 'history']
    .map((key) => layers[key])
    .filter((layer): layer is RuntimeOutboxLayer => !!layer && layer.total > 0);

  return (
    <section className={`worker-health-panel ${tone}`}>
      <div className="wh-head">
        <div className="wh-title-wrap">
          <span className={`wh-status ${tone}`}>
            {tone === 'err' ? <AlertTriangle size={15} /> : tone === 'warn' ? <Clock size={15} /> : <CheckCircle2 size={15} />}
            {summary?.label || 'Worker 读取中'}
          </span>
          <p>{summary?.detail || '正在读取运行队列、worker 心跳和最近执行请求趋势。'}</p>
        </div>
        <div className="wh-head-actions">
          <button className="wh-scan" type="button" onClick={onScan}>
            <Compass size={13} />巡检
          </button>
          {!!data?.failed && (
            <button className="wh-archive-all" type="button" onClick={onArchiveAll}>
              <FolderArchive size={13} />归档失败
            </button>
          )}
        </div>
      </div>

      {!!visibleLayers.length && (
        <div className="wh-layer-row">
          {visibleLayers.map((layer) => {
            const cause = blockingLayerLabel(layer.blockingLayer);
            return (
              <div key={layer.key} className={`wh-layer ${layer.key}`}>
                <span>{layer.label}</span>
                <b>{layer.pending}/{layer.running}/{layer.failed}</b>
                <em>{layer.detail}</em>
                {cause && <small>{cause}</small>}
              </div>
            );
          })}
        </div>
      )}

      <div className="wh-grid">
        <div className="wh-cell">
          <span>Worker</span>
          <b>{worker?.active ? '运行中' : '未运行'}</b>
          <em>{heartbeat}</em>
        </div>
        <div className="wh-cell">
          <span>队列 P/R/F</span>
          <b>{data ? `${data.pending}/${data.running}/${data.failed}` : '0/0/0'}</b>
          <em>total {data?.total || 0}</em>
        </div>
        <div className="wh-cell">
          <span>最旧 Pending</span>
          <b>{data?.oldestPendingAgeText || '0秒'}</b>
          <em>{(data?.oldestPendingAgeSec || 0) >= 600 ? '需要处理' : '正常'}</em>
        </div>
        <div className="wh-cell">
          <span>最旧 Running</span>
          <b>{data?.oldestRunningAgeText || '0秒'}</b>
          <em>{(data?.oldestRunningAgeSec || 0) >= 600 ? '可能卡住' : '正常'}</em>
        </div>
        <div className="wh-cell">
          <span>趋势</span>
          <b>{data?.trend?.label || '15分钟 入0 成0 败0'}</b>
          <em>{summary?.nextAction || '无需处理'}</em>
        </div>
      </div>

      {!!activeItems.length && (
        <div className="wh-section">
          <div className="wh-section-title">
            <Activity size={14} />活跃队列
          </div>
          <div className="wh-list">
            {activeItems.slice(0, 4).map((item) => (
              <QueueItem key={item.id} item={item} tone="active" onOpenTask={onOpenTask} />
            ))}
          </div>
        </div>
      )}

      {!!deadLetters.length && (
        <div className="wh-section">
          <div className="wh-section-title err">
            <AlertTriangle size={14} />失败执行请求
            {deadClipped && <span>显示 {deadDisplayed}/{deadTotal}</span>}
          </div>
          <div className="wh-list">
            {visibleDeadLetters.map((item) => (
              <QueueItem
                key={item.id}
                item={item}
                tone="failed"
                onOpenTask={onOpenTask}
                onRetry={onRetry}
                onArchive={onArchive}
              />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
