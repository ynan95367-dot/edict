import { formatDashboardTime } from '../../time';
import type { TaskEvidenceData } from '../../api';

const AGENT_LABELS: Record<string, string> = {
  main: '太子',
  zhongshu: '中书省',
  menxia: '门下省',
  shangshu: '尚书省',
  libu: '礼部',
  hubu: '户部',
  bingbu: '兵部',
  xingbu: '刑部',
  gongbu: '工部',
  libu_hr: '吏部',
  zaochao: '钦天监',
};

function agentLabel(agent?: string) {
  if (!agent) return '';
  return AGENT_LABELS[agent] || agent;
}

function shortTrace(traceId?: string): string {
  if (!traceId) return '—';
  return traceId.length > 18 ? `${traceId.slice(0, 8)}…${traceId.slice(-6)}` : traceId;
}

function outboxLabel(outbox?: { pending?: number; running?: number; failed?: number; total?: number } | null): string {
  if (!outbox) return '空';
  const parts: string[] = [];
  if (outbox.running) parts.push(`执行${outbox.running}`);
  if (outbox.pending) parts.push(`待办${outbox.pending}`);
  if (outbox.failed) parts.push(`失败${outbox.failed}`);
  return parts.join(' · ') || '空';
}

function fmtActivityTime(ts: number | string | undefined): string {
  return formatDashboardTime(ts, { showSeconds: true });
}

function evidenceLaneLabel(lane?: string): string {
  const labels: Record<string, string> = {
    state: '状态',
    governance: '流转',
    dispatch: '执行',
    session: '会话',
    tool: '工具',
    file: '文件',
    test: '测试',
    model: '模型',
    event: '事件',
  };
  return labels[lane || ''] || lane || '事件';
}

function evidenceStatusLabel(status?: string): string {
  if (status === 'ok') return '正常';
  if (status === 'warn') return '注意';
  if (status === 'err') return '异常';
  return '待观测';
}

export function EvidenceChainSection({ data }: { data: TaskEvidenceData | null }) {
  if (!data) {
    return (
      <div className="evidence-section idle">
        <div className="evidence-head">
          <div>
            <span className="run-title">证据链</span>
            <p>正在合并任务、执行请求、OpenCode session、模型和工具证据。</p>
          </div>
          <span className="evidence-pill idle">读取中</span>
        </div>
      </div>
    );
  }

  if (!data.ok) {
    return (
      <div className="evidence-section err">
        <div className="evidence-head">
          <div>
            <span className="run-title">证据链</span>
            <p>{data.error || '证据读取失败'}</p>
          </div>
          <span className="evidence-pill err">异常</span>
        </div>
      </div>
    );
  }

  const health = data.health || { status: 'idle', label: '待观测' };
  const status = health.status || 'idle';
  const summary = data.summary;
  const timeline = (data.timeline || []).slice(-10).reverse();
  const sessions = data.sessions || [];
  const models = data.models || [];
  const missing = data.missingLayers || [];
  const activeOutbox =
    (summary?.outboxRunning || 0) + (summary?.outboxPending || 0) + (summary?.outboxFailed || 0);
  const modelLine = models.length
    ? `${models.length} 个关联 · 异常 ${summary?.modelFailures || 0}`
    : '无关联模型记录';

  return (
    <div className={`evidence-section ${status}`}>
      <div className="evidence-head">
        <div>
          <span className="run-title">证据链</span>
          <p>{health.detail || '把任务运行证据汇总到同一条 trace 下。'}</p>
        </div>
        <span className={`evidence-pill ${status}`}>{health.label || evidenceStatusLabel(status)}</span>
      </div>

      <div className="evidence-callout">
        <b>{evidenceStatusLabel(status)}：{health.label || '任务证据已汇总'}</b>
        <span>{health.nextAction || '继续观察任务进展。'}</span>
      </div>

      <div className="evidence-grid">
        <div className="evidence-cell"><span>Trace</span><b className="mono">{shortTrace(data.traceId)}</b></div>
        <div className="evidence-cell"><span>Session</span><b>{sessions.length ? `${sessions.length} 个` : '未绑定'}</b></div>
        <div className="evidence-cell"><span>队列</span><b className={summary?.outboxFailed ? 'tone-err' : activeOutbox ? 'tone-warn' : 'tone-ok'}>{summary ? outboxLabel({ pending: summary.outboxPending, running: summary.outboxRunning, failed: summary.outboxFailed }) : '空'}</b></div>
        <div className="evidence-cell"><span>模型</span><b className={summary?.modelFailures ? 'tone-err' : 'tone-idle'}>{modelLine}</b></div>
        <div className="evidence-cell"><span>文件</span><b>{summary?.fileCount || 0} 个 · 输出 {summary?.outputCount || 0}</b></div>
        <div className="evidence-cell"><span>命令/测试</span><b>{summary?.commandCount || 0} / {summary?.testCount || 0}</b></div>
      </div>

      {sessions.length > 0 && (
        <div className="evidence-strip">
          {sessions.slice(-4).map((session) => (
            <span key={session.sessionId} className={`evidence-token ${session.status || 'idle'}`}>
              {agentLabel(session.agentId)} · {shortTrace(session.sessionId)}
            </span>
          ))}
        </div>
      )}

      {missing.length > 0 && (
        <div className="evidence-gap">
          {missing.slice(0, 4).map((item) => <span key={item}>{item}</span>)}
        </div>
      )}

      <div className="evidence-timeline">
        {timeline.length ? timeline.map((item, idx) => (
          <div className={`evidence-event ${item.status || 'idle'}`} key={`${item.at || ''}-${item.lane}-${idx}`}>
            <span className="evidence-time">{fmtActivityTime(item.at)}</span>
            <span className="evidence-lane">{evidenceLaneLabel(item.lane)}</span>
            <b>{item.title}</b>
            <em>{item.detail || item.source || '已记录'}</em>
          </div>
        )) : (
          <div className="evidence-empty">暂无可展示证据</div>
        )}
      </div>
    </div>
  );
}
