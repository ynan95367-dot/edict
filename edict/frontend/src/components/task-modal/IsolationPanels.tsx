import type { ExecutionIsolation, IsolationHealth, WorktreeCheckpoint } from '../../api';
import { shortPath } from './codingSessionUtils';

export function IsolationHealthStrip({ health }: { health?: IsolationHealth }) {
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

export function CheckpointStrip({ checkpoint }: { checkpoint?: WorktreeCheckpoint }) {
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

export function IsolationStrip({ isolation }: { isolation?: ExecutionIsolation }) {
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
