import { CheckCircle2, FileDiff, XCircle } from 'lucide-react';
import { api } from '../../api';
import { formatDashboardTime } from '../../time';
import type {
  CodingEvent,
  CodingSessionData,
  ExecutionIsolation,
  IsolationHealth,
  PatchReview,
  WorktreeCheckpoint,
} from '../../api';

function fmtActivityTime(ts: number | string | undefined): string {
  return formatDashboardTime(ts, { showSeconds: true });
}

function shortTrace(traceId?: string): string {
  if (!traceId) return '—';
  return traceId.length > 18 ? `${traceId.slice(0, 8)}…${traceId.slice(-6)}` : traceId;
}

function codingKindLabel(kind: string): string {
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

function codingKindIcon(kind: string): string {
  if (kind.startsWith('file.')) return kind === 'file.read' ? '📖' : '✏️';
  if (kind.startsWith('test.')) return '🧪';
  if (kind === 'shell.run') return '⌘';
  if (kind.startsWith('output.')) return '📦';
  if (kind.startsWith('todo.')) return '☑';
  if (kind.startsWith('governance.')) return '🏛️';
  if (kind === 'tool.search') return '🔎';
  return '•';
}

function shortPath(path: string): string {
  if (!path) return '';
  const parts = path.replace(/\\/g, '/').split('/');
  return parts.length > 3 ? `…/${parts.slice(-3).join('/')}` : path;
}

function lineLabel(startLine?: number, endLine?: number): string {
  if (!startLine) return '';
  return endLine && endLine !== startLine ? `L${startLine}-${endLine}` : `L${startLine}`;
}

function sessionStatusLabel(status?: string): string {
  if (status === 'bound') return '已绑定';
  if (status === 'trace-mismatch') return 'Trace 不一致';
  if (status === 'observed') return '已观测';
  if (status === 'unbound') return '未绑定';
  return status || '未绑定';
}

function IsolationHealthStrip({ health }: { health?: IsolationHealth }) {
  if (!health) return null;
  const status = health.status || 'idle';
  const cls = status === 'ok' ? 'optional' : 'required';
  const bits = [
    health.worktreeReady ? 'Worktree 就绪' : health.worktreePath ? 'Worktree 待检查' : '',
    health.patchRequired ? (health.patchReviewReady ? 'Patch 已记录' : 'Patch 待生成') : '',
    health.rollbackReady ? 'Rollback 可追溯' : '',
  ].filter(Boolean);
  return (
    <div className={`isolation-strip ${cls}`}>
      <span>闭环</span>
      <b className={`tone-${status}`}>{health.label || '隔离状态未知'}</b>
      <em>{health.detail || health.nextAction || '等待隔离证据'}</em>
      {!!bits.length && (
        <div className="isolation-tags">
          {bits.map((item) => <span key={item}>{item}</span>)}
        </div>
      )}
    </div>
  );
}

function CheckpointStrip({ checkpoint }: { checkpoint?: WorktreeCheckpoint }) {
  if (!checkpoint) return null;
  if (!checkpoint.ok) {
    return (
      <div className="checkpoint-strip warn">
        <span>Worktree</span>
        <b>不可用</b>
        <em>{checkpoint.error || '无法读取 git 状态'}</em>
      </div>
    );
  }
  const files = checkpoint.files || [];
  const dirty = !!checkpoint.dirty;
  const changeLabel = dirty ? `${checkpoint.fileCount || files.length} 个变更` : '干净';
  return (
    <div className={`checkpoint-strip ${dirty ? 'dirty' : 'clean'}`}>
      <span>Worktree</span>
      <b>{checkpoint.branch || 'HEAD'}{checkpoint.head ? ` · ${checkpoint.head}` : ''}</b>
      <em>
        {changeLabel}
        {dirty ? ` · 暂存 ${checkpoint.stagedCount || 0} · 未暂存 ${checkpoint.unstagedCount || 0} · 未跟踪 ${checkpoint.untrackedCount || 0}` : ''}
      </em>
      {!!files.length && (
        <div className="checkpoint-files">
          {files.slice(0, 4).map((file) => (
            <span key={`${file.status}-${file.path}`}>{file.status} {shortPath(file.path)}</span>
          ))}
          {(checkpoint.fileCount || 0) > 4 && <span>+{(checkpoint.fileCount || 0) - 4}</span>}
        </div>
      )}
    </div>
  );
}

function IsolationStrip({ isolation }: { isolation?: ExecutionIsolation }) {
  if (!isolation?.mode) return null;
  const required = !!isolation.required;
  const parts = [
    isolation.patchFirst ? 'Patch-first' : '',
    isolation.requiresPatchReview ? '需要审批' : '',
    isolation.checkpoint ? `Checkpoint ${isolation.checkpoint}` : '',
    isolation.rollback ? `Rollback ${isolation.rollback}` : '',
    isolation.worktreeBranch ? `Branch ${isolation.worktreeBranch}` : '',
  ].filter(Boolean);
  return (
    <div className={`isolation-strip ${required ? 'required' : 'optional'}`}>
      <span>Isolation</span>
      <b>{isolation.label || isolation.mode}</b>
      <em>{isolation.worktreePath || isolation.reason || '执行隔离策略已随 RunSpec 生成'}</em>
      {!!parts.length && (
        <div className="isolation-tags">
          {parts.map((item) => <span key={item}>{item}</span>)}
        </div>
      )}
    </div>
  );
}

export function CodingSessionSection({
  data,
  onOpenSource,
  onCreatePatch,
  onDecidePatch,
}: {
  data: CodingSessionData | null;
  onOpenSource: (path: string, startLine?: number, endLine?: number) => void;
  onCreatePatch: (paths: string[]) => void;
  onDecidePatch: (patchId: string, action: 'approve' | 'reject') => void;
}) {
  if (!data?.ok) return null;
  const s = data.summary;
  const recent = data.events.slice(-12).reverse();
  const filePreview = data.files.slice(0, 5);
  const outputPreview = data.outputs.slice(0, 4);
  const commandPreview = [...data.commands, ...data.tests].slice(-4).reverse();
  const runtimeSession = data.runtimeSession || {};
  const sessionStatus = runtimeSession.status || (data.sessionId ? 'bound' : 'unbound');
  const sessionTone = sessionStatus === 'trace-mismatch' ? 'err' : sessionStatus === 'bound' || sessionStatus === 'observed' ? 'ok' : 'warn';

  return (
    <div className="cockpit">
      <div className="cockpit-head">
        <div>
          <div className="cockpit-title">执行证据</div>
          <div className="cockpit-sub">
            {data.runtime || 'runtime'} · session <span className={`mono tone-${sessionTone}`}>{data.sessionId ? shortTrace(data.sessionId) : '未绑定'}</span>
            {data.traceId && <span> · trace <span className="mono">{shortTrace(data.traceId)}</span></span>}
          </div>
        </div>
        <div className={`cockpit-mode ${s.hasPatchReview ? 'ok' : 'warn'}`}>
          {sessionStatusLabel(sessionStatus)} · {s.hasPatchReview ? `Patch 审批已接入${s.pendingPatchCount ? ` · 待审 ${s.pendingPatchCount}` : ''}` : '尚未接入 Patch 审批'}
        </div>
      </div>

      <div className="cockpit-grid">
        <div className="cockpit-cell"><span>Todo</span><b>{s.todoDone}/{s.todoTotal}</b></div>
        <div className="cockpit-cell"><span>文件</span><b>{s.fileCount}</b></div>
        <div className="cockpit-cell"><span>命令</span><b>{s.commandCount}</b></div>
        <div className="cockpit-cell"><span>测试</span><b>{s.testCount}</b></div>
        <div className="cockpit-cell"><span>产物</span><b>{s.outputCount}</b></div>
        <div className="cockpit-cell"><span>事件</span><b>{s.eventCount}</b></div>
      </div>

      <IsolationHealthStrip health={data.isolationHealth} />
      <IsolationStrip isolation={data.executionIsolation} />
      <CheckpointStrip checkpoint={data.checkpoint} />

      <PatchReviewPanel data={data} onCreatePatch={onCreatePatch} onDecidePatch={onDecidePatch} />

      <div className="cockpit-columns">
        <div className="cockpit-panel">
          <div className="cockpit-label">文件与产物</div>
          {!filePreview.length && !outputPreview.length ? (
            <div className="cockpit-empty">暂无文件事件</div>
          ) : (
            <>
              {outputPreview.map((e) => <CodingFileRow key={`out-${e.path || e.title}`} event={e} />)}
              {filePreview.map((f) => (
                <div className="cockpit-row" key={f.path}>
                  <span className="cockpit-row-icon">📄</span>
                  {f.sourceUrl ? (
                    <button
                      className="cockpit-row-main link as-button"
                      onClick={() => onOpenSource(f.path, f.lastStartLine || 0, f.lastEndLine || 0)}
                    >
                      {shortPath(f.path)}
                    </button>
                  ) : (
                    <span className="cockpit-row-main">{shortPath(f.path)}</span>
                  )}
                  <span className="cockpit-row-meta">
                    {lineLabel(f.lastStartLine, f.lastEndLine) || `读 ${f.reads} · 改 ${f.changes} · 出 ${f.outputs}`}
                  </span>
                </div>
              ))}
            </>
          )}
        </div>

        <div className="cockpit-panel">
          <div className="cockpit-label">命令与测试</div>
          {!commandPreview.length ? (
            <div className="cockpit-empty">暂无命令事件</div>
          ) : (
            commandPreview.map((e) => (
              <div className="cockpit-row" key={`${e.kind}-${e.at}-${e.title}`}>
                <span className="cockpit-row-icon">{codingKindIcon(e.kind)}</span>
                <span className="cockpit-row-main">{e.command || e.title}</span>
                <span className={`cockpit-row-meta ${e.status === 'fail' ? 'err' : e.status === 'pass' ? 'ok' : ''}`}>{codingKindLabel(e.kind)}</span>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="cockpit-events">
        <div className="cockpit-label">最近执行事件</div>
        {!recent.length ? (
          <div className="cockpit-empty">暂无事件</div>
        ) : (
          recent.map((e) => (
            <CodingEventRow
              key={`${e.kind}-${e.at}-${e.title}-${e.path}`}
              event={e}
              onOpenSource={onOpenSource}
            />
          ))
        )}
      </div>

      {!!data.missingLayers.length && (
        <div className="cockpit-gap">
          待补：{data.missingLayers.join(' · ')}
        </div>
      )}
    </div>
  );
}

function patchStatusLabel(status: string) {
  if (status === 'pending') return '待审';
  if (status === 'approved') return '已准奏';
  if (status === 'rejected') return '已驳回';
  return status || '未知';
}

function patchTone(status: string) {
  if (status === 'pending') return 'warn';
  if (status === 'approved') return 'ok';
  if (status === 'rejected') return 'err';
  return 'idle';
}

function patchFileMix(review: PatchReview) {
  const files = review.stats?.files || [];
  const added = files.filter((f) => f.status === 'added').length;
  const deleted = files.filter((f) => f.status === 'deleted' || (!f.insertions && f.deletions > 0)).length;
  const parts = [];
  if (added) parts.push(`新增 ${added}`);
  if (deleted) parts.push(`删除 ${deleted}`);
  return parts.join(' · ');
}

function PatchReviewPanel({
  data,
  onCreatePatch,
  onDecidePatch,
}: {
  data: CodingSessionData;
  onCreatePatch: (paths: string[]) => void;
  onDecidePatch: (patchId: string, action: 'approve' | 'reject') => void;
}) {
  const reviews = data.patchReviews || [];
  const changedPaths = data.files.filter((f) => f.changes > 0).map((f) => f.path);
  const latest = [...reviews].reverse().slice(0, 3);
  return (
    <div className="patch-panel">
      <div className="patch-head">
        <div>
          <div className="patch-title"><FileDiff size={14} />Patch 审批</div>
          <div className="patch-sub">
            {reviews.length
              ? `共 ${reviews.length} 个审批，待审 ${data.summary.pendingPatchCount || 0}`
              : changedPaths.length
                ? `${changedPaths.length} 个文件修改可生成审批`
                : '等待 Agent 上报文件修改事件'}
          </div>
        </div>
        <button
          className="patch-create"
          type="button"
          disabled={!changedPaths.length}
          onClick={() => onCreatePatch(changedPaths)}
        >
          <FileDiff size={13} />生成审批
        </button>
      </div>
      {!latest.length ? (
        <div className="patch-empty">暂无 Patch 审批记录</div>
      ) : (
        <div className="patch-list">
          {latest.map((review) => <PatchReviewRow key={review.id} review={review} onDecidePatch={onDecidePatch} />)}
        </div>
      )}
    </div>
  );
}

function PatchReviewRow({
  review,
  onDecidePatch,
}: {
  review: PatchReview;
  onDecidePatch: (patchId: string, action: 'approve' | 'reject') => void;
}) {
  const fileCount = review.paths?.length || review.stats?.files?.length || 0;
  const insertions = review.stats?.insertions || 0;
  const deletions = review.stats?.deletions || 0;
  const fileMix = patchFileMix(review);
  const patchLocation = review.worktreeBranch || (review.worktreePath ? 'task-worktree' : '');
  return (
    <div className="patch-row">
      <div className="patch-main">
        <span className={`patch-status ${patchTone(review.status)}`}>{patchStatusLabel(review.status)}</span>
        <span className="patch-name">{fileCount} 文件{fileMix ? ` · ${fileMix}` : ''} · +{insertions} -{deletions}</span>
        <span className="patch-meta mono" title={review.worktreePath || undefined}>
          {shortTrace(review.id)}{review.baseHead ? ` · ${review.baseHead}` : ''}{patchLocation ? ` · ${patchLocation}` : ''}
        </span>
      </div>
      <div className="patch-paths">{(review.paths || []).slice(0, 3).map(shortPath).join(' · ')}</div>
      {review.lastError && <div className="patch-error">{review.lastError}</div>}
      {review.status === 'pending' && (
        <div className="patch-actions">
          <button className="patch-act ok" type="button" onClick={() => onDecidePatch(review.id, 'approve')}>
            <CheckCircle2 size={13} />准奏
          </button>
          <button className="patch-act danger" type="button" onClick={() => onDecidePatch(review.id, 'reject')}>
            <XCircle size={13} />驳回
          </button>
        </div>
      )}
      {!!review.diffPreview && (
        <details className="patch-diff">
          <summary>查看 diff</summary>
          <pre>{review.diffPreview}</pre>
        </details>
      )}
    </div>
  );
}

function CodingFileRow({ event }: { event: CodingEvent }) {
  const file = event.meta || {};
  const path = event.path || String(file.path || '');
  const href = path ? api.outputFileUrl(path) : '';
  return (
    <div className="cockpit-row">
      <span className="cockpit-row-icon">📦</span>
      {href ? (
        <a className="cockpit-row-main link" href={href} target="_blank" rel="noreferrer">{shortPath(path)}</a>
      ) : (
        <span className="cockpit-row-main">{event.title}</span>
      )}
      <span className="cockpit-row-meta">{event.status || 'ready'}</span>
    </div>
  );
}

function CodingEventRow({
  event,
  onOpenSource,
}: {
  event: CodingEvent;
  onOpenSource: (path: string, startLine?: number, endLine?: number) => void;
}) {
  const main = event.path ? shortPath(event.path) : event.command || event.title;
  const canOpen = !!event.sourceUrl && !!event.path;
  return (
    <div className="cockpit-event">
      <span className="cockpit-event-time">{fmtActivityTime(event.at)}</span>
      <span className="cockpit-event-kind">{codingKindIcon(event.kind)} {codingKindLabel(event.kind)}</span>
      {canOpen ? (
        <button
          className="cockpit-event-main link as-button"
          onClick={() => onOpenSource(event.path, event.startLine, event.endLine)}
        >
          {main}{lineLabel(event.startLine, event.endLine) ? ` · ${lineLabel(event.startLine, event.endLine)}` : ''}
        </button>
      ) : (
        <span className="cockpit-event-main">{main}</span>
      )}
      {event.detail && <span className="cockpit-event-detail">{event.detail}</span>}
    </div>
  );
}
