import type { ActivityEntry, SchedulerInfo } from '../../api';
import { formatDashboardTime } from '../../time';

export const AGENT_LABELS: Record<string, string> = {
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

export const NEXT_LABELS: Record<string, string> = {
  Taizi: '中书省起草',
  Zhongshu: '门下省审议',
  Menxia: '尚书省派发',
  Assigned: '开始执行',
  Doing: '进入审查',
  Review: '完成',
};

export const DISPATCH_LABELS: Record<string, { label: string; tone: 'ok' | 'warn' | 'err' | 'idle' }> = {
  queued: { label: '派发排队中', tone: 'warn' },
  progress: { label: 'Agent 已有进展', tone: 'ok' },
  success: { label: '最近派发成功', tone: 'ok' },
  idle: { label: '等待调度', tone: 'idle' },
  failed: { label: '派发失败', tone: 'err' },
  timeout: { label: '派发超时', tone: 'err' },
  error: { label: '派发异常', tone: 'err' },
  'gateway-offline': { label: '运行时未启动', tone: 'err' },
  'openclaw-missing': { label: 'OpenClaw CLI 缺失', tone: 'err' },
  'opencode-missing': { label: 'OpenCode CLI 缺失', tone: 'err' },
  'opencode-session-stale': { label: 'OpenCode 会话失效', tone: 'warn' },
};

export const SCHED_ACTION_LABELS: Record<string, string> = {
  scan: '立即扫描',
  retry: '重试派发',
  escalate: '升级协调',
  rollback: '回滚',
};

export type SchedulerActionFeedback = {
  action: string;
  label: string;
  detail: string;
  tone: 'ok' | 'warn' | 'err' | 'idle';
  pending?: boolean;
};

export function dispatchInfo(sched?: SchedulerInfo | null) {
  const raw = sched?.lastDispatchStatus || 'idle';
  return DISPATCH_LABELS[raw] || { label: raw, tone: 'warn' as const };
}

export function agentLabel(agent?: string) {
  if (!agent) return '';
  return AGENT_LABELS[agent] || agent;
}

export function fmtStalled(sec: number): string {
  const v = Math.max(0, sec);
  if (v < 60) return `${v}秒`;
  if (v < 3600) return `${Math.floor(v / 60)}分${v % 60}秒`;
  const h = Math.floor(v / 3600);
  const m = Math.floor((v % 3600) / 60);
  return `${h}小时${m}分`;
}

export function activityKey(a: ActivityEntry): string {
  const at = String(a.at || '');
  if (a.eventId) return `event:${a.eventId}`;
  if (a.kind === 'flow') return ['flow', at, a.from || '', a.to || '', a.remark || ''].join('|');
  if (a.kind === 'progress') return ['progress', at, a.agent || '', a.text || ''].join('|');
  if (a.kind === 'tool_result') return ['tool', at, a.agent || '', a.tool || '', (a.output || '').slice(0, 80)].join('|');
  return [a.kind, at, a.agent || '', a.text || a.thinking || a.eventKind || ''].join('|');
}

export function compactActivity(activity: ActivityEntry[]): ActivityEntry[] {
  const seen = new Set<string>();
  const out: ActivityEntry[] = [];
  for (const item of activity) {
    if (item.kind === 'todos') continue;
    const key = activityKey(item);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out.slice(-80);
}

export function fmtActivityTime(ts: number | string | undefined): string {
  return formatDashboardTime(ts, { showSeconds: true });
}

export function shortTrace(traceId?: string): string {
  if (!traceId) return '—';
  return traceId.length > 18 ? `${traceId.slice(0, 8)}…${traceId.slice(-6)}` : traceId;
}

export function outboxLabel(outbox?: { pending?: number; running?: number; failed?: number; total?: number } | null): string {
  if (!outbox) return '空';
  const parts: string[] = [];
  if (outbox.running) parts.push(`执行${outbox.running}`);
  if (outbox.pending) parts.push(`待发${outbox.pending}`);
  if (outbox.failed) parts.push(`失败${outbox.failed}`);
  return parts.join(' · ') || '空';
}

export function runtimeSessionTone(status?: string): 'ok' | 'warn' | 'err' | 'idle' {
  if (status === 'bound') return 'ok';
  if (status === 'trace-mismatch') return 'err';
  if (status === 'unbound') return 'idle';
  if (status) return 'warn';
  return 'idle';
}

export function runtimeSessionLabel(status?: string): string {
  if (status === 'bound') return '已绑定';
  if (status === 'trace-mismatch') return 'Trace 不一致';
  if (status === 'unbound') return '未绑定';
  return status || '未绑定';
}
