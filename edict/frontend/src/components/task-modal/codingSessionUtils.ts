import { formatDashboardTime } from '../../time';
import type { PatchReview } from '../../api';

export function fmtActivityTime(ts: number | string | undefined): string {
  return formatDashboardTime(ts, { showSeconds: true });
}

export function shortTrace(traceId?: string): string {
  if (!traceId) return '—';
  return traceId.length > 18 ? `${traceId.slice(0, 8)}…${traceId.slice(-6)}` : traceId;
}

export function codingKindLabel(kind: string): string {
  const map: Record<string, string> = {
    'todo.item': 'Todo',
    'file.read': '读文件',
    'file.change': '改文件',
    'tool.search': '搜索',
    'shell.run': '命令',
    'test.run': '测试',
    'test.result': '测试结果',
    'tool.result': '工具结果',
    'output.file': '产物',
    'output.note': '说明',
    'message.progress': '进展',
    'governance.flow': '流转',
  };
  return map[kind] || kind;
}

export function codingKindIcon(kind: string): string {
  if (kind.startsWith('file.')) return kind === 'file.read' ? '📖' : '✏️';
  if (kind.startsWith('test.')) return '🧪';
  if (kind === 'shell.run') return '⌘';
  if (kind.startsWith('output.')) return '📦';
  if (kind.startsWith('todo.')) return '☑';
  if (kind.startsWith('governance.')) return '🏛️';
  if (kind === 'tool.search') return '🔎';
  return '•';
}

export function shortPath(path: string): string {
  if (!path) return '';
  const parts = path.replace(/\\/g, '/').split('/');
  return parts.length > 3 ? `…/${parts.slice(-3).join('/')}` : path;
}

export function lineLabel(startLine?: number, endLine?: number): string {
  if (!startLine) return '';
  return endLine && endLine !== startLine ? `L${startLine}-${endLine}` : `L${startLine}`;
}

export function sessionStatusLabel(status?: string): string {
  if (status === 'bound') return '已绑定';
  if (status === 'trace-mismatch') return 'Trace 不一致';
  if (status === 'observed') return '已观测';
  if (status === 'unbound') return '未绑定';
  return status || '未绑定';
}

export function patchStatusLabel(status: string) {
  if (status === 'pending') return '待审';
  if (status === 'approved') return '已准奏';
  if (status === 'rejected') return '已驳回';
  return status || '未知';
}

export function patchTone(status: string) {
  if (status === 'pending') return 'warn';
  if (status === 'approved') return 'ok';
  if (status === 'rejected') return 'err';
  return 'idle';
}

export function patchFileMix(review: PatchReview) {
  const files = review.stats?.files || [];
  const added = files.filter((f) => f.status === 'added').length;
  const deleted = files.filter((f) => f.status === 'deleted' || (!f.insertions && f.deletions > 0)).length;
  const parts = [];
  if (added) parts.push(`新增 ${added}`);
  if (deleted) parts.push(`删除 ${deleted}`);
  return parts.join(' · ');
}
