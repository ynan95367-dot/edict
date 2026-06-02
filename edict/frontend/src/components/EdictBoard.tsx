import { useStore, isEdict, isArchived, getPipeStatus, stateLabel, deptColor, PIPE } from '../store';
import { api, type Task } from '../api';
import {
  AlertTriangle,
  Archive,
  ArchiveRestore,
  Ban,
  CalendarClock,
  CheckCircle2,
  Compass,
  FolderArchive,
  Gauge,
  Pause,
  Play,
  RotateCcw,
  Route,
} from 'lucide-react';

// 排序权重
const STATE_ORDER: Record<string, number> = {
  Doing: 0, Review: 1, Assigned: 2, Menxia: 3, Zhongshu: 4,
  Taizi: 5, Inbox: 6, Blocked: 7, Next: 8, Done: 9, Cancelled: 10,
};

const DISPATCH_STATUS: Record<string, { label: string; tone: 'ok' | 'warn' | 'err' | 'idle' }> = {
  queued: { label: '派发排队', tone: 'warn' },
  progress: { label: '已有进展', tone: 'ok' },
  success: { label: '派发成功', tone: 'ok' },
  idle: { label: '待调度', tone: 'idle' },
  failed: { label: '派发失败', tone: 'err' },
  timeout: { label: '派发超时', tone: 'err' },
  error: { label: '派发异常', tone: 'err' },
  'gateway-offline': { label: '运行时未启动', tone: 'err' },
  'openclaw-missing': { label: 'OpenClaw 缺失', tone: 'err' },
  'opencode-missing': { label: 'OpenCode 缺失', tone: 'err' },
};

function dispatchBadge(task: Task) {
  const sched = task._scheduler;
  const status = sched?.lastDispatchStatus || '';
  if (!status || ['success', 'progress', 'idle'].includes(status)) return null;
  return DISPATCH_STATUS[status] || { label: status, tone: 'warn' as const };
}

function MiniPipe({ task }: { task: Task }) {
  const stages = getPipeStatus(task);
  return (
    <div className="ec-pipe">
      {stages.map((s, i) => (
        <span key={s.key} style={{ display: 'contents' }}>
          <div className={`ep-node ${s.status}`}>
            <div className="ep-icon">{i + 1}</div>
            <div className="ep-name">{s.dept}</div>
          </div>
          {i < stages.length - 1 && <div className="ep-arrow">›</div>}
        </span>
      ))}
    </div>
  );
}

function EdictCard({ task }: { task: Task }) {
  const setModalTaskId = useStore((s) => s.setModalTaskId);
  const toast = useStore((s) => s.toast);
  const loadAll = useStore((s) => s.loadAll);

  const hb = task.heartbeat || { status: 'unknown', label: '⚪' };
  const stCls = 'st-' + (task.state || '');
  const deptCls = 'dt-' + (task.org || '').replace(/\s/g, '');
  const curStage = PIPE.find((_, i) => getPipeStatus(task)[i].status === 'active');
  const todos = task.todos || [];
  const todoDone = todos.filter((x) => x.status === 'completed').length;
  const todoTotal = todos.length;
  const canStop = !['Done', 'Blocked', 'Cancelled'].includes(task.state);
  const canResume = ['Blocked', 'Cancelled'].includes(task.state);
  const archived = isArchived(task);
  const isBlocked = task.block && task.block !== '无' && task.block !== '-';
  const dBadge = dispatchBadge(task);

  const handleAction = async (action: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (action === 'stop' || action === 'cancel') {
      // Use confirm dialog via store (will implement with ConfirmDialog)
      const reason = prompt(action === 'stop' ? '请输入叫停原因：' : '请输入取消原因：');
      if (reason === null) return;
      try {
        const r = await api.taskAction(task.id, action, reason);
        if (r.ok) { toast(r.message || '操作成功'); loadAll(); }
        else toast(r.error || '操作失败', 'err');
      } catch { toast('服务器连接失败', 'err'); }
    } else if (action === 'resume') {
      try {
        const r = await api.taskAction(task.id, 'resume', '恢复执行');
        if (r.ok) { toast(r.message || '已恢复'); loadAll(); }
        else toast(r.error || '操作失败', 'err');
      } catch { toast('服务器连接失败', 'err'); }
    }
  };

  const handleArchive = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      const r = await api.archiveTask(task.id, !task.archived);
      if (r.ok) { toast(r.message || '操作成功'); loadAll(); }
      else toast(r.error || '操作失败', 'err');
    } catch { toast('服务器连接失败', 'err'); }
  };

  return (
    <div
      className={`edict-card state-${task.state || 'Unknown'}${archived ? ' archived' : ''}`}
      onClick={() => setModalTaskId(task.id)}
    >
      <div className="ec-topline">
        <div className="ec-id">{task.id}</div>
        <span className={`tag ${stCls}`}>{stateLabel(task)}</span>
      </div>
      <div className="ec-title">{task.title || '(无标题)'}</div>
      <MiniPipe task={task} />
      <div className="ec-meta">
        {task.org && <span className={`tag ${deptCls}`}>{task.org}</span>}
        {curStage && (
          <span className="ec-current">
            当前: <b style={{ color: deptColor(curStage.dept) }}>{curStage.dept} · {curStage.action}</b>
          </span>
        )}
      </div>
      {task.now && task.now !== '-' && (
        <div style={{ fontSize: 11, color: 'var(--muted)', lineHeight: 1.5, marginBottom: 6 }}>
          {task.now.substring(0, 80)}
        </div>
      )}
      {(task.review_round || 0) > 0 && (
        <div style={{ fontSize: 11, marginBottom: 6 }}>
          {Array.from({ length: task.review_round || 0 }, (_, i) => (
            <span
              key={i}
              style={{
                display: 'inline-block', width: 14, height: 14, borderRadius: '50%',
                background: i < (task.review_round || 0) - 1 ? '#1a3a6a22' : 'var(--acc)22',
                border: `1px solid ${i < (task.review_round || 0) - 1 ? '#2a4a8a' : 'var(--acc)'}`,
                fontSize: 9, textAlign: 'center', lineHeight: '13px', marginRight: 2,
                color: i < (task.review_round || 0) - 1 ? '#4a6aaa' : 'var(--acc)',
              }}
            >
              {i + 1}
            </span>
          ))}
          <span style={{ color: 'var(--muted)', fontSize: 10 }}>第 {task.review_round} 轮磋商</span>
        </div>
      )}
      {todoTotal > 0 && (
        <div className="ec-todo-bar">
          <span>{todoDone}/{todoTotal}</span>
          <div className="ec-todo-track">
            <div className="ec-todo-fill" style={{ width: `${Math.round((todoDone / todoTotal) * 100)}%` }} />
          </div>
          <span>{todoDone === todoTotal ? '全部完成' : '进行中'}</span>
        </div>
      )}
      <div className="ec-footer">
        <span className={`hb ${hb.status}`}>{hb.label}</span>
        {dBadge && <span className={`dispatch-pill ${dBadge.tone}`}>{dBadge.label}</span>}
        {isBlocked && (
          <span className="tag" style={{ borderColor: '#ff527044', color: 'var(--danger)', background: '#200a10' }}>
            {task.block}
          </span>
        )}
        {task.eta && task.eta !== '-' && (
          <span className="ec-eta"><CalendarClock size={12} /> {task.eta}</span>
        )}
      </div>
      <div className="ec-actions" onClick={(e) => e.stopPropagation()}>
        {canStop && (
          <>
            <button className="mini-act" onClick={(e) => handleAction('stop', e)}><Pause size={12} />叫停</button>
            <button className="mini-act danger" onClick={(e) => handleAction('cancel', e)}><Ban size={12} />取消</button>
          </>
        )}
        {canResume && (
          <button className="mini-act" onClick={(e) => handleAction('resume', e)}><Play size={12} />恢复</button>
        )}
        {archived && !task.archived && (
          <button className="mini-act" onClick={handleArchive}><Archive size={12} />归档</button>
        )}
        {task.archived && (
          <button className="mini-act" onClick={handleArchive}><ArchiveRestore size={12} />取消归档</button>
        )}
      </div>
    </div>
  );
}

export default function EdictBoard() {
  const liveStatus = useStore((s) => s.liveStatus);
  const edictFilter = useStore((s) => s.edictFilter);
  const setEdictFilter = useStore((s) => s.setEdictFilter);
  const setModalTaskId = useStore((s) => s.setModalTaskId);
  const runtimeOutbox = useStore((s) => s.runtimeOutbox);
  const toast = useStore((s) => s.toast);
  const loadAll = useStore((s) => s.loadAll);

  const tasks = liveStatus?.tasks || [];
  const allEdicts = tasks.filter(isEdict);
  const activeEdicts = allEdicts.filter((t) => !isArchived(t));
  const archivedEdicts = allEdicts.filter((t) => isArchived(t));
  const routingEdicts = activeEdicts.filter((t) => ['Inbox', 'Taizi', 'Zhongshu', 'Menxia', 'Assigned'].includes(t.state));
  const runningEdicts = activeEdicts.filter((t) => ['Doing', 'Review'].includes(t.state));
  const blockedEdicts = activeEdicts.filter((t) => t.state === 'Blocked' || (t.block && t.block !== '无' && t.block !== '-'));

  let edicts: Task[];
  if (edictFilter === 'active') edicts = activeEdicts;
  else if (edictFilter === 'archived') edicts = archivedEdicts;
  else edicts = allEdicts;

  edicts.sort((a, b) => (STATE_ORDER[a.state] ?? 9) - (STATE_ORDER[b.state] ?? 9));

  const unArchivedDone = allEdicts.filter((t) => !t.archived && ['Done', 'Cancelled'].includes(t.state));
  const summaryCards = [
    { key: 'active', label: '活跃旨意', value: activeEdicts.length, sub: `${routingEdicts.length} 道待派发`, tone: 'jade', icon: Gauge },
    { key: 'running', label: '执行/审查', value: runningEdicts.length, sub: '正在消化的任务', tone: 'gold', icon: Route },
    { key: 'blocked', label: '阻塞风险', value: blockedEdicts.length, sub: blockedEdicts.length ? '需要人工看一眼' : '目前干净', tone: blockedEdicts.length ? 'coral' : 'ok', icon: AlertTriangle },
    { key: 'archived', label: '归档沉淀', value: archivedEdicts.length, sub: `总计 ${allEdicts.length} 道`, tone: 'violet', icon: FolderArchive },
  ];

  const handleArchiveAll = async () => {
    if (!confirm('将所有已完成/已取消的旨意移入归档？')) return;
    try {
      const r = await api.archiveAllDone();
      if (r.ok) { toast(`📦 ${r.count || 0} 道旨意已归档`); loadAll(); }
      else toast(r.error || '批量归档失败', 'err');
    } catch { toast('服务器连接失败', 'err'); }
  };

  const handleScan = async () => {
    try {
      const r = await api.schedulerScan();
      if (r.ok) toast(`🧭 太子巡检完成：${r.count || 0} 个动作`);
      else toast(r.error || '巡检失败', 'err');
      loadAll();
    } catch { toast('服务器连接失败', 'err'); }
  };

  const handleOutboxRetry = async (itemId: string) => {
    try {
      const r = await api.runtimeOutboxRetry(itemId, 'dashboard dead-letter retry');
      if (r.ok) {
        toast('失败派发已重新入队');
        loadAll();
      } else {
        toast(r.error || '重新入队失败', 'err');
      }
    } catch {
      toast('服务器连接失败', 'err');
    }
  };

  const handleOutboxArchive = async (itemId: string) => {
    try {
      const r = await api.runtimeOutboxArchive({ itemId, reason: 'dashboard dead-letter archive' });
      if (r.ok) {
        toast(r.message || '失败派发已归档');
        loadAll();
      } else {
        toast(r.error || '归档失败', 'err');
      }
    } catch {
      toast('服务器连接失败', 'err');
    }
  };

  const handleOutboxArchiveAll = async () => {
    if (!confirm(`归档全部 ${runtimeOutbox?.failed || 0} 条失败派发？归档后不再显示在死信面板，但仍保留追溯记录。`)) return;
    try {
      const r = await api.runtimeOutboxArchive({ archiveAllFailed: true, reason: 'dashboard dead-letter archive all' });
      if (r.ok) {
        toast(`已归档 ${r.count || 0} 条失败派发`);
        loadAll();
      } else {
        toast(r.error || '归档失败', 'err');
      }
    } catch {
      toast('服务器连接失败', 'err');
    }
  };

  const deadWindow = runtimeOutbox?.deadLetterWindow;
  const deadReturned = deadWindow?.returned || runtimeOutbox?.deadLetters?.length || 0;
  const deadTotal = deadWindow?.total || runtimeOutbox?.failed || 0;

  return (
    <div>
      {!!runtimeOutbox?.failed && (
        <div className="deadletter-panel">
          <div className="dl-head">
            <div>
              <div className="dl-title">派发死信</div>
              <div className="dl-sub">
                {runtimeOutbox.failed} 个 outbox 项失败，最常见原因是运行时未启动、CLI 缺失或 Agent 会话报错。
                {deadWindow?.truncated && ` 当前显示 ${deadReturned}/${deadTotal} 条。`}
              </div>
            </div>
            <div className="dl-head-actions">
              <button className="dl-scan" type="button" onClick={handleScan}>
                <Compass size={13} />巡检
              </button>
              <button className="dl-archive-all" type="button" onClick={handleOutboxArchiveAll}>
                <FolderArchive size={13} />归档全部
              </button>
            </div>
          </div>
          <div className="dl-list">
            {runtimeOutbox.deadLetters.map((item) => (
              <div className="dl-item" key={item.id}>
                <button className="dl-main" type="button" onClick={() => item.taskId && setModalTaskId(item.taskId)}>
                  <span className="dl-id">{item.taskId || item.id}</span>
                  <span className="dl-name">{item.taskTitle || item.trigger || item.kind}</span>
                  <span className="dl-meta">
                    {item.kind} · {item.agentId || '未指定'} · {item.attempts || 0}/{item.maxAttempts || 0}
                  </span>
                </button>
                <span className="dl-error">{item.lastError || '无错误详情'}</span>
                <div className="dl-actions">
                  <button className="dl-retry" type="button" onClick={() => handleOutboxRetry(item.id)}>
                    <RotateCcw size={13} />重试
                  </button>
                  <button className="dl-archive" type="button" onClick={() => handleOutboxArchive(item.id)}>
                    <Archive size={13} />归档
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="board-summary">
        {summaryCards.map((card) => {
          const Icon = card.icon;
          return (
            <button
              key={card.key}
              type="button"
              className={`bs-card ${card.tone}${card.key === edictFilter ? ' active' : ''}`}
              onClick={() => {
                if (card.key === 'active') setEdictFilter('active');
                if (card.key === 'archived') setEdictFilter('archived');
                if (card.key === 'running' || card.key === 'blocked') setEdictFilter('all');
              }}
            >
              <span className="bs-icon"><Icon size={16} /></span>
              <span className="bs-copy">
                <span className="bs-label">{card.label}</span>
                <span className="bs-sub">{card.sub}</span>
              </span>
              <b>{card.value}</b>
            </button>
          );
        })}
      </div>

      {/* Archive Bar */}
      <div className="archive-bar">
        <div className="ab-filter">
          <span className="ab-label">筛选</span>
          {(['active', 'archived', 'all'] as const).map((f) => (
            <button
              key={f}
              className={`ab-btn ${edictFilter === f ? 'active' : ''}`}
              onClick={() => setEdictFilter(f)}
            >
              {f === 'active' ? '活跃' : f === 'archived' ? '归档' : '全部'}
            </button>
          ))}
          {unArchivedDone.length > 0 && (
            <button className="ab-btn" onClick={handleArchiveAll}><Archive size={13} />一键归档</button>
          )}
        </div>
        <span className="ab-count">
          活跃 {activeEdicts.length} · 归档 {archivedEdicts.length} · 共 {allEdicts.length}
        </span>
        <button className="ab-scan" onClick={handleScan}><Compass size={13} />太子巡检</button>
      </div>

      {/* Grid */}
      <div className="edict-grid">
        {edicts.length === 0 ? (
          <div className="empty empty-edicts" style={{ gridColumn: '1/-1' }}>
            <CheckCircle2 size={28} />
            <div className="empty-title">
              {edictFilter === 'active' ? '当前没有活跃旨意' : edictFilter === 'archived' ? '归档里暂时为空' : '暂无旨意'}
            </div>
            <div className="empty-copy">
              {edictFilter === 'active' && archivedEdicts.length > 0
                ? `已有 ${archivedEdicts.length} 道历史旨意在归档中，可以切到“全部”或“归档”查看。`
                : '通过飞书向太子发送任务，太子分拣后转中书省处理。'}
            </div>
            {edictFilter === 'active' && archivedEdicts.length > 0 && (
              <div className="empty-actions">
                <button type="button" className="empty-btn primary" onClick={() => setEdictFilter('all')}>
                  查看全部
                </button>
                <button type="button" className="empty-btn" onClick={() => setEdictFilter('archived')}>
                  查看归档
                </button>
              </div>
            )}
          </div>
        ) : (
          edicts.map((t) => <EdictCard key={t.id} task={t} />)
        )}
      </div>
    </div>
  );
}
