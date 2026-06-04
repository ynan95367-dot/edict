import { Ban, CheckCircle2, Pause, Play, SkipForward, XCircle } from 'lucide-react';

type TaskActionsBarProps = {
  taskState: string;
  canStop: boolean;
  canResume: boolean;
  onStop: () => void;
  onCancel: () => void;
  onResume: () => void;
  onReview: (action: 'approve' | 'reject') => void;
  onAdvance: () => void;
};

export function TaskActionsBar({
  taskState,
  canStop,
  canResume,
  onStop,
  onCancel,
  onResume,
  onReview,
  onAdvance,
}: TaskActionsBarProps) {
  const canReview = ['Review', 'Menxia'].includes(taskState);
  const canAdvance = ['Pending', 'Taizi', 'Zhongshu', 'Menxia', 'Assigned', 'Doing', 'Review', 'Next'].includes(taskState);

  return (
    <div className="task-actions">
      {canStop && (
        <>
          <button className="btn-action btn-stop" onClick={onStop}><Pause size={14} />叫停任务</button>
          <button className="btn-action btn-cancel" onClick={onCancel}><Ban size={14} />取消任务</button>
        </>
      )}
      {canResume && (
        <button className="btn-action btn-resume" onClick={onResume}><Play size={14} />恢复执行</button>
      )}
      {canReview && (
        <>
          <button className="btn-action" style={{ background: '#2ecc8a22', color: '#2ecc8a', border: '1px solid #2ecc8a44' }} onClick={() => onReview('approve')}><CheckCircle2 size={14} />准奏</button>
          <button className="btn-action" style={{ background: '#ff527022', color: '#ff5270', border: '1px solid #ff527044' }} onClick={() => onReview('reject')}><XCircle size={14} />封驳</button>
        </>
      )}
      {canAdvance && (
        <button className="btn-action" style={{ background: '#7c5cfc18', color: '#7c5cfc', border: '1px solid #7c5cfc44' }} onClick={onAdvance}><SkipForward size={14} />推进到下一步</button>
      )}
    </div>
  );
}
