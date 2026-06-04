import type { TodoItem } from '../../api';

type TodoSectionProps = {
  todos: TodoItem[];
  todoDone: number;
  todoTotal: number;
};

export function TodoSection({ todos, todoDone, todoTotal }: TodoSectionProps) {
  return (
    <div className="todo-section">
      <div className="todo-header">
        <div className="m-sec-label" style={{ marginBottom: 0, border: 'none', padding: 0 }}>
          子任务清单（{todoDone}/{todoTotal}）
        </div>
        <div className="todo-progress">
          <div className="todo-bar">
            <div className="todo-bar-fill" style={{ width: `${Math.round((todoDone / todoTotal) * 100)}%` }} />
          </div>
          <span>{Math.round((todoDone / todoTotal) * 100)}%</span>
        </div>
      </div>
      <div className="todo-list">
        {todos.map((td) => {
          const ico = td.status === 'completed' ? '✅' : td.status === 'in-progress' ? '🔄' : '⬜';
          const stLabel = td.status === 'completed' ? '已完成' : td.status === 'in-progress' ? '进行中' : '待开始';
          const stCls = td.status === 'completed' ? 's-done' : td.status === 'in-progress' ? 's-progress' : 's-notstarted';
          const itemCls = td.status === 'completed' ? 'done' : '';
          return (
            <div className={`todo-item ${itemCls}`} key={td.id}>
              <div className="t-row">
                <span className="t-icon">{ico}</span>
                <span className="t-id">#{td.id}</span>
                <span className="t-title">{td.title}</span>
                <span className={`t-status ${stCls}`}>{stLabel}</span>
              </div>
              {td.detail && <div className="todo-detail">{td.detail}</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
