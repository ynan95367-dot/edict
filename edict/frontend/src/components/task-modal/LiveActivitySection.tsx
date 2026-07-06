import type { CSSProperties, RefObject } from 'react';
import type { ActivityEntry, ActivityToolCall, ActivityToolRun, TaskActivityData } from '../../api';
import { formatDashboardDateTime } from '../../time';
import {
  activityKey,
  AGENT_LABELS,
  compactActivity,
  fmtActivityTime,
  outboxLabel,
  shortTrace,
} from './taskModalUtils';

type LiveActivitySectionProps = {
  data: TaskActivityData | null;
  isDone: boolean;
  logRef: RefObject<HTMLDivElement | null>;
};

export function LiveActivitySection({ data, isDone, logRef }: LiveActivitySectionProps) {
  if (!data) return null;

  const activity = data.activity || [];
  const isActive = (() => {
    if (!activity.length) return false;
    const last = activity[activity.length - 1];
    if (!last.at) return false;
    const ts = typeof last.at === 'number' ? last.at : new Date(last.at).getTime();
    return Date.now() - ts < 300000;
  })();

  const agentParts: string[] = [];
  if (data.agentLabel) agentParts.push(data.agentLabel);
  if (data.relatedAgents && data.relatedAgents.length > 1) agentParts.push(`${data.relatedAgents.length}个 Agent`);
  if (data.lastActive) agentParts.push(`最后活跃: ${formatDashboardDateTime(data.lastActive)}`);

  const phaseDurations = data.phaseDurations || [];
  const ts = data.todosSummary;
  const rs = data.resourceSummary;
  const evidence = data.stateEvidence;
  const evidenceTone = evidence?.confidence === 'high' || evidence?.confidence === 'complete'
    ? 'ok'
    : evidence?.confidence === 'medium'
      ? 'warn'
      : 'err';
  const currentPhase = phaseDurations.find((p) => p.ongoing) || phaseDurations[phaseDurations.length - 1];
  const toolRuns = data.toolRuns || [];
  const pairedToolRunIds = new Set(toolRuns.map((run) => run.toolRunId).filter(Boolean));
  const timeline = compactActivity(activity).filter((entry) => shouldShowTimelineEntry(entry, pairedToolRunIds));

  return (
    <div className="la-section">
      <div className="la-header">
        <span className="la-title">
          <span className={`la-dot${isActive ? '' : ' idle'}`} />
          {isDone ? '执行回顾' : '实时动态'}
        </span>
        <span className="la-agent">{agentParts.join(' · ') || '加载中...'}</span>
      </div>

      <div className="la-insights">
        {evidence && (
          <div className={`li-item ${evidenceTone}`}>
            <span>证据</span>
            <b>{evidence.label}</b>
            <em>{evidence.eventCount || 0} 条事件</em>
          </div>
        )}
        {currentPhase && (
          <div className="li-item">
            <span>当前耗时</span>
            <b>{currentPhase.phase}</b>
            <em>{currentPhase.durationText}{currentPhase.ongoing ? ' · 进行中' : ''}</em>
          </div>
        )}
        {ts && (
          <div className="li-item">
            <span>子任务</span>
            <b>{ts.percent}%</b>
            <em>{ts.completed}/{ts.total} 完成</em>
          </div>
        )}
        {rs && (rs.totalTokens || rs.totalCost || rs.totalElapsedSec) && (
          <div className="li-item">
            <span>资源</span>
            <b>{rs.totalTokens != null ? rs.totalTokens.toLocaleString() : '—'}</b>
            <em>{rs.totalCost != null ? `$${rs.totalCost.toFixed(4)}` : ''}{rs.totalElapsedSec != null ? ` · ${rs.totalElapsedSec}s` : ''}</em>
          </div>
        )}
        {data.totalDuration && (
          <div className="li-item">
            <span>总耗时</span>
            <b>{data.totalDuration}</b>
            <em>{phaseDurations.length} 个阶段</em>
          </div>
        )}
        {data.traceSummary?.traceId && (
          <div className={`li-item ${data.traceSummary.outbox?.failed ? 'err' : data.traceSummary.outbox?.pending || data.traceSummary.outbox?.running ? 'warn' : 'ok'}`}>
            <span>Trace</span>
            <b className="mono">{shortTrace(data.traceSummary.traceId)}</b>
            <em>{outboxLabel(data.traceSummary.outbox)}</em>
          </div>
        )}
        {data.activityWindow?.truncated && (
          <div className="li-item">
            <span>动态</span>
            <b>{data.activityWindow.returned || activity.length}/{data.activityWindow.total || activity.length}</b>
            <em>已折叠低信号记录</em>
          </div>
        )}
      </div>

      {ts && (
        <div className="la-progress-line">
          <div style={{ width: `${ts.total ? (ts.completed / ts.total) * 100 : 0}%` }} />
          <div className="active" style={{ width: `${ts.total ? (ts.inProgress / ts.total) * 100 : 0}%` }} />
        </div>
      )}

      {toolRuns.length > 0 && (
        <div className="la-toolruns">
          <div className="la-toolruns-head">
            <span>工具执行</span>
            <b>{toolRuns.length} 条</b>
          </div>
          <div className="la-toolrun-grid">
            {toolRuns.slice(-6).reverse().map((run) => <ToolRunCard key={run.toolRunId} run={run} />)}
          </div>
        </div>
      )}

      <div className="la-log" ref={logRef as RefObject<HTMLDivElement>}>
        {timeline.length > 0 ? (
          timeline.map((a, i) => <ActivityEntryView key={`${activityKey(a)}-${i}`} entry={a} />)
        ) : (
          <div className="la-empty">
            {data.message || data.error || 'Agent 尚未上报进展（等待 Agent 调用 progress 命令）'}
          </div>
        )}
      </div>
    </div>
  );
}

function shouldShowTimelineEntry(entry: ActivityEntry, pairedToolRunIds: Set<string>): boolean {
  if (!pairedToolRunIds.size) return true;
  if (entry.kind === 'tool_result' && entry.toolRunId && pairedToolRunIds.has(entry.toolRunId)) return false;
  if (entry.kind === 'assistant' && entry.tools?.length && !entry.text && !entry.thinking) {
    return !entry.tools.every((tool) => tool.toolRunId && pairedToolRunIds.has(tool.toolRunId));
  }
  return true;
}

function ActivityEntryView({ entry: a }: { entry: ActivityEntry }) {
  const time = fmtActivityTime(a.at);
  const agBadge = a.agent ? (
    <span style={{ fontSize: 9, color: 'var(--muted)', background: 'var(--panel)', padding: '1px 4px', borderRadius: 3, marginRight: 4 }}>
      {AGENT_LABELS[a.agent] || a.agent}
    </span>
  ) : null;

  if (a.kind === 'flow') {
    return (
      <div className="la-entry la-flow">
        <span className="la-icon">↳</span>
        <span className="la-body">
          <b>{a.from || '系统'}</b> → <b>{a.to || '未指定'}</b>
          {a.remark ? <span className="la-rem"> {a.remark}</span> : null}
        </span>
        <span className="la-time">{time}</span>
      </div>
    );
  }

  if (a.kind === 'progress') {
    return (
      <div className="la-entry la-assistant">
        <span className="la-icon">🔄</span>
        <span className="la-body">{agBadge}<b>当前进展：</b>{a.text}</span>
        <span className="la-time">{time}</span>
      </div>
    );
  }

  if (a.kind === 'todos') {
    const items = a.items || [];
    const diffMap = new Map<string, { type: string; from?: string; to?: string }>();
    if (a.diff) {
      (a.diff.changed || []).forEach((c) => diffMap.set(c.id, { type: 'changed', from: c.from, to: c.to }));
      (a.diff.added || []).forEach((c) => diffMap.set(c.id, { type: 'added' }));
    }
    return (
      <div className="la-entry" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 2 }}>
        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 2 }}>{agBadge}📝 执行计划</div>
        {items.map((td) => {
          const icon = td.status === 'completed' ? '✅' : td.status === 'in-progress' ? '🔄' : '⬜';
          const d = diffMap.get(String(td.id));
          const style: CSSProperties = td.status === 'completed'
            ? { opacity: 0.5, textDecoration: 'line-through' }
            : td.status === 'in-progress'
              ? { color: '#60a5fa', fontWeight: 'bold' }
              : {};
          return (
            <div key={td.id} style={style}>
              {icon} {td.title}
              {d && d.type === 'changed' && d.to === 'completed' && <span style={{ color: '#22c55e', fontSize: 9, marginLeft: 4 }}>✨刚完成</span>}
              {d && d.type === 'changed' && d.to !== 'completed' && <span style={{ color: '#f59e0b', fontSize: 9, marginLeft: 4 }}>↻{d.from}→{d.to}</span>}
              {d && d.type === 'added' && <span style={{ color: '#3b82f6', fontSize: 9, marginLeft: 4 }}>🆕新增</span>}
            </div>
          );
        })}
        {a.diff?.removed?.map((r) => (
          <div key={r.id} style={{ opacity: 0.4, textDecoration: 'line-through' }}>🗑 {r.title}</div>
        ))}
      </div>
    );
  }

  if (a.kind === 'assistant') {
    return (
      <>
        {a.thinking && (
          <div className="la-entry la-thinking">
            <span className="la-icon">⋯</span>
            <span className="la-body">
              {agBadge}
              <details className="la-evidence">
                <summary>行动证据：{a.thinking.length > 120 ? `${a.thinking.slice(0, 120)}…` : a.thinking}</summary>
                <div>{a.thinking}</div>
              </details>
            </span>
            <span className="la-time">{time}</span>
          </div>
        )}
        {a.tools?.map((tc, i) => (
          <div className={`la-entry la-tool ${toolTone(tc.status)}`} key={toolCallKey(a, tc, i)}>
            <span className="la-icon">🔧</span>
            <span className="la-body">
              {agBadge}
              <span className="la-tool-head">
                <span className="la-tool-name">{tc.name}</span>
                <span className={`la-tool-status ${toolTone(tc.status)}`}>{tc.statusLabel || toolStatusLabel(tc.status)}</span>
              </span>
              <span className="la-trunc">{toolSummary(tc)}</span>
            </span>
            <span className="la-time">{time}</span>
          </div>
        ))}
        {a.text && (
          <div className="la-entry la-assistant">
            <span className="la-icon">🤖</span>
            <span className="la-body">{agBadge}{a.text}</span>
            <span className="la-time">{time}</span>
          </div>
        )}
      </>
    );
  }

  if (a.kind === 'tool_result') {
    const ok = a.exitCode === 0 || a.exitCode === null || a.exitCode === undefined;
    return (
      <div className={`la-entry la-tool-result ${ok ? 'ok' : 'err'}`}>
        <span className="la-icon">{ok ? '✅' : '❌'}</span>
        <span className="la-body">
          {agBadge}
          <span className="la-tool-head">
            <span className="la-tool-name">{a.tool || 'tool'}</span>
            <span className={`la-tool-status ${toolTone(a.status, a.exitCode)}`}>{a.statusLabel || toolStatusLabel(a.status, a.exitCode)}</span>
            {a.durationMs ? <span className="la-tool-duration">{formatDuration(a.durationMs)}</span> : null}
          </span>
          <span className="la-trunc">{toolResultSummary(a)}</span>
        </span>
        <span className="la-time">{time}</span>
      </div>
    );
  }

  if (a.kind === 'user') {
    return (
      <div className="la-entry la-user">
        <span className="la-icon">📥</span>
        <span className="la-body">{agBadge}{a.text || ''}</span>
        <span className="la-time">{time}</span>
      </div>
    );
  }

  return (
    <div className="la-entry la-tool">
      <span className="la-icon">•</span>
      <span className="la-body">{agBadge}<span className="la-tool-name">{a.eventKind || a.kind}</span>{a.text || a.remark || ''}</span>
      <span className="la-time">{time}</span>
    </div>
  );
}

function toolTone(status?: string, exitCode?: number | null): 'ok' | 'warn' | 'err' | 'idle' {
  if (exitCode !== undefined && exitCode !== null && exitCode !== 0) return 'err';
  if (status === 'completed') return 'ok';
  if (status === 'running' || status === 'started' || status === 'queued' || status === 'pending') return 'warn';
  if (status === 'failed' || status === 'error' || status === 'rejected' || status === 'denied') return 'err';
  return 'idle';
}

function toolStatusLabel(status?: string, exitCode?: number | null): string {
  if (exitCode !== undefined && exitCode !== null && exitCode !== 0) return '异常';
  if (status === 'completed') return '已完成';
  if (status === 'running' || status === 'started') return '执行中';
  if (status === 'queued' || status === 'pending') return '排队中';
  if (status === 'rejected' || status === 'denied') return '已拒绝';
  if (status === 'failed' || status === 'error') return '异常';
  return '已记录';
}

function compact(value?: string, max = 170): string {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  if (!text) return '';
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const sec = ms / 1000;
  return sec < 60 ? `${sec.toFixed(sec >= 10 ? 0 : 1)}s` : `${Math.floor(sec / 60)}m${Math.round(sec % 60)}s`;
}

function toolSummary(tool: ActivityToolCall): string {
  return compact(tool.inputSummary || tool.input_preview || tool.command || tool.path || '');
}

function toolResultSummary(entry: ActivityEntry): string {
  return compact(entry.output || entry.command || entry.path || entry.inputSummary || '');
}

function toolCallKey(entry: ActivityEntry, tool: ActivityToolCall, index: number): string {
  if (tool.toolRunId) return `tool-call:${tool.toolRunId}`;
  if (tool.callId) return `tool-call:${tool.callId}`;
  return `${activityKey(entry)}:tool:${tool.name}:${index}`;
}

function ToolRunCard({ run }: { run: ActivityToolRun }) {
  const tone = toolTone(run.status, run.exitCode);
  const primary = compact(run.inputSummary || run.command || run.path || run.tool || '工具调用', 150);
  const result = compact(run.output || '', 170);
  const time = fmtActivityTime(run.endedAt || run.startedAt);
  return (
    <div className={`la-toolrun ${tone}`}>
      <div className="la-toolrun-top">
        <span className="la-toolrun-name">{run.tool || 'tool'}</span>
        <span className={`la-tool-status ${tone}`}>{run.statusLabel || toolStatusLabel(run.status, run.exitCode)}</span>
        {run.durationMs ? <span className="la-tool-duration">{formatDuration(run.durationMs)}</span> : null}
      </div>
      <div className="la-toolrun-main">{primary}</div>
      {result && <div className="la-toolrun-result">{result}</div>}
      <div className="la-toolrun-meta">
        {run.agent ? <span>{AGENT_LABELS[run.agent] || run.agent}</span> : null}
        {time ? <span>{time}</span> : null}
      </div>
    </div>
  );
}
