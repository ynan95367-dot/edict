import type { Task } from '../../api';
import { stateLabel } from '../../store';

type TaskNotesSectionProps = {
  task: Task;
};

export function TaskNotesSection({ task }: TaskNotesSectionProps) {
  if (!(task.now || task.ac || task.block || task.eta || (task.review_round || 0) > 0)) {
    return null;
  }

  return (
    <div className="m-section">
      <div className="m-sec-label">任务要点</div>
      <div className="m-rows concise">
        <div className="m-row">
          <div className="mr-label">状态</div>
          <div className="mr-val">
            <span className={`tag st-${task.state}`}>{stateLabel(task)}</span>
            {(task.review_round || 0) > 0 && <span className="muted-inline">磋商 {task.review_round} 轮</span>}
          </div>
        </div>
        {task.eta && task.eta !== '-' && (
          <div className="m-row"><div className="mr-label">预计完成</div><div className="mr-val">{task.eta}</div></div>
        )}
        {task.block && task.block !== '无' && task.block !== '-' && (
          <div className="m-row alert" style={{ gridColumn: '1/-1' }}><div className="mr-label">阻塞项</div><div className="mr-val">{task.block}</div></div>
        )}
        {task.now && task.now !== '-' && (
          <div className="m-row" style={{ gridColumn: '1/-1' }}>
            <div className="mr-label">当前进展</div>
            <div className="mr-val normal">{task.now}</div>
          </div>
        )}
        {task.ac && (
          <div className="m-row" style={{ gridColumn: '1/-1' }}>
            <div className="mr-label">验收标准</div>
            <div className="mr-val normal">{task.ac}</div>
          </div>
        )}
      </div>
    </div>
  );
}
