import { CheckCircle2, FileDiff, XCircle } from 'lucide-react';
import type { CodingSessionData, PatchReview } from '../../api';
import { patchFileMix, patchStatusLabel, patchTone, shortPath, shortTrace } from './codingSessionUtils';

type PatchReviewPanelProps = {
  data: CodingSessionData;
  onCreatePatch: (paths: string[]) => void;
  onDecidePatch: (patchId: string, action: 'approve' | 'reject') => void;
};

export function PatchReviewPanel({ data, onCreatePatch, onDecidePatch }: PatchReviewPanelProps) {
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
