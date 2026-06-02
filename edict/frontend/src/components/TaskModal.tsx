import { useEffect, useState, useRef, useCallback } from 'react';
import {
  ArrowUpCircle,
  Ban,
  CheckCircle2,
  Download,
  ExternalLink,
  FileDiff,
  FileText,
  Pause,
  Play,
  Package,
  RotateCcw,
  Search,
  SkipForward,
  Undo2,
  X,
  XCircle,
} from 'lucide-react';
import { useStore, getPipeStatus, deptColor, stateLabel, STATE_LABEL } from '../store';
import { api } from '../api';
import { formatDashboardDateTime, formatDashboardTime } from '../time';
import type {
  Task,
  TaskActivityData,
  SchedulerStateData,
  ActivityEntry,
  TodoItem,
  SchedulerInfo,
  CodingSessionData,
  CodingEvent,
  PatchReview,
  SourceFileResult,
  WorktreeCheckpoint,
  OutputGroup,
} from '../api';

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

const NEXT_LABELS: Record<string, string> = {
  Taizi: '中书省起草',
  Zhongshu: '门下省审议',
  Menxia: '尚书省派发',
  Assigned: '开始执行',
  Doing: '进入审查',
  Review: '完成',
};

const DISPATCH_LABELS: Record<string, { label: string; tone: 'ok' | 'warn' | 'err' | 'idle' }> = {
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

function dispatchInfo(sched?: SchedulerInfo | null) {
  const raw = sched?.lastDispatchStatus || 'idle';
  return DISPATCH_LABELS[raw] || { label: raw, tone: 'warn' as const };
}

function agentLabel(agent?: string) {
  if (!agent) return '';
  return AGENT_LABELS[agent] || agent;
}

function fmtStalled(sec: number): string {
  const v = Math.max(0, sec);
  if (v < 60) return `${v}秒`;
  if (v < 3600) return `${Math.floor(v / 60)}分${v % 60}秒`;
  const h = Math.floor(v / 3600);
  const m = Math.floor((v % 3600) / 60);
  return `${h}小时${m}分`;
}

function activityKey(a: ActivityEntry): string {
  const at = String(a.at || '');
  if (a.eventId) return `event:${a.eventId}`;
  if (a.kind === 'flow') return ['flow', at, a.from || '', a.to || '', a.remark || ''].join('|');
  if (a.kind === 'progress') return ['progress', at, a.agent || '', a.text || ''].join('|');
  if (a.kind === 'tool_result') return ['tool', at, a.agent || '', a.tool || '', (a.output || '').slice(0, 80)].join('|');
  return [a.kind, at, a.agent || '', a.text || a.thinking || a.eventKind || ''].join('|');
}

function compactActivity(activity: ActivityEntry[]): ActivityEntry[] {
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

function fmtActivityTime(ts: number | string | undefined): string {
  return formatDashboardTime(ts, { showSeconds: true });
}

function shortTrace(traceId?: string): string {
  if (!traceId) return '—';
  return traceId.length > 18 ? `${traceId.slice(0, 8)}…${traceId.slice(-6)}` : traceId;
}

function outboxLabel(outbox?: { pending?: number; running?: number; failed?: number; total?: number } | null): string {
  if (!outbox) return '空';
  const parts: string[] = [];
  if (outbox.running) parts.push(`执行${outbox.running}`);
  if (outbox.pending) parts.push(`待发${outbox.pending}`);
  if (outbox.failed) parts.push(`失败${outbox.failed}`);
  return parts.join(' · ') || '空';
}

export default function TaskModal() {
  const modalTaskId = useStore((s) => s.modalTaskId);
  const setModalTaskId = useStore((s) => s.setModalTaskId);
  const liveStatus = useStore((s) => s.liveStatus);
  const loadAll = useStore((s) => s.loadAll);
  const toast = useStore((s) => s.toast);

  const [activityData, setActivityData] = useState<TaskActivityData | null>(null);
  const [schedData, setSchedData] = useState<SchedulerStateData | null>(null);
  const [codingData, setCodingData] = useState<CodingSessionData | null>(null);
  const [sourcePreview, setSourcePreview] = useState<SourceFileResult | null>(null);
  const laTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  const task = liveStatus?.tasks?.find((t) => t.id === modalTaskId) || null;

  const fetchActivity = useCallback(async () => {
    if (!modalTaskId) return;
    try {
      const d = await api.taskActivity(modalTaskId);
      setActivityData(d);
    } catch {
      setActivityData(null);
    }
  }, [modalTaskId]);

  const fetchSched = useCallback(async () => {
    if (!modalTaskId) return;
    try {
      const d = await api.schedulerState(modalTaskId);
      setSchedData(d);
    } catch {
      setSchedData(null);
    }
  }, [modalTaskId]);

  const fetchCodingSession = useCallback(async () => {
    if (!modalTaskId) return;
    try {
      const d = await api.codingSession(modalTaskId);
      setCodingData(d);
    } catch {
      setCodingData(null);
    }
  }, [modalTaskId]);

  const openSourcePreview = useCallback(async (path: string, startLine = 0, endLine = 0) => {
    if (!path) return;
    try {
      const data = await api.sourceFile(path, startLine, endLine);
      setSourcePreview(data);
    } catch {
      toast('源码片段无法读取或不在项目目录内', 'err');
    }
  }, [toast]);

  const openSourceInEditor = useCallback(async (path: string, startLine = 0) => {
    if (!path) return;
    try {
      const result = await api.openSourceFile(path, startLine);
      if (result.ok) toast(result.message || '已请求编辑器打开源码', 'ok');
      else toast(result.error || '编辑器打开失败', 'err');
    } catch {
      toast('编辑器打开接口不可用', 'err');
    }
  }, [toast]);

  const createPatchReview = useCallback(async (paths: string[]) => {
    if (!modalTaskId) return;
    try {
      const result = await api.createPatchReview(modalTaskId, paths);
      if (result.ok) {
        toast('Patch 审批已生成', 'ok');
        fetchCodingSession();
      } else {
        toast(result.error || 'Patch 审批生成失败', 'err');
      }
    } catch {
      toast('Patch 审批接口不可用', 'err');
    }
  }, [modalTaskId, fetchCodingSession, toast]);

  const decidePatchReview = useCallback(async (patchId: string, action: 'approve' | 'reject') => {
    const label = action === 'approve' ? '准奏' : '驳回并回滚';
    if (action === 'reject' && !confirm('驳回会尝试把这个 patch 反向应用到当前工作区，确认继续？')) return;
    try {
      const result = await api.patchReviewAction(patchId, action, label);
      if (result.ok) {
        toast(result.message || `Patch 已${label}`, 'ok');
        fetchCodingSession();
        loadAll();
      } else {
        toast(result.error || `Patch ${label}失败`, 'err');
        fetchCodingSession();
      }
    } catch {
      toast('Patch 审批接口不可用', 'err');
    }
  }, [fetchCodingSession, loadAll, toast]);

  useEffect(() => {
    if (!modalTaskId || !task) return;
    setSourcePreview(null);
    fetchActivity();
    fetchSched();
    fetchCodingSession();

    const isDone = ['Done', 'Cancelled'].includes(task.state);
    if (!isDone) {
      laTimerRef.current = setInterval(() => {
        fetchActivity();
        fetchSched();
        fetchCodingSession();
      }, 4000);
    }

    return () => {
      if (laTimerRef.current) {
        clearInterval(laTimerRef.current);
        laTimerRef.current = null;
      }
    };
  }, [modalTaskId, task?.state, fetchActivity, fetchSched, fetchCodingSession]);

  // scroll log on new entries
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [activityData?.activity?.length]);

  if (!modalTaskId || !task) return null;

  const close = () => setModalTaskId(null);

  const stages = getPipeStatus(task);
  const activeStage = stages.find((s) => s.status === 'active');
  const hb = task.heartbeat || { status: 'unknown' as const, label: '⚪ 无数据' };
  const todos = task.todos || [];
  const todoDone = todos.filter((x) => x.status === 'completed').length;
  const todoTotal = todos.length;
  const canStop = !['Done', 'Blocked', 'Cancelled'].includes(task.state);
  const canResume = ['Blocked', 'Cancelled'].includes(task.state);

  const doTaskAction = async (action: string, reason: string) => {
    try {
      const r = await api.taskAction(task.id, action, reason);
      if (r.ok) {
        toast(r.message || '操作成功', 'ok');
        loadAll();
        close();
      } else {
        toast(r.error || '操作失败', 'err');
      }
    } catch {
      toast('服务器连接失败', 'err');
    }
  };

  const doReview = async (action: string) => {
    const labels: Record<string, string> = { approve: '准奏', reject: '封驳' };
    const comment = prompt(`${labels[action]} ${task.id}\n\n请输入批注（可留空）：`);
    if (comment === null) return;
    try {
      const r = await api.reviewAction(task.id, action, comment || '');
      if (r.ok) {
        toast(`✅ ${task.id} 已${labels[action]}`, 'ok');
        loadAll();
        close();
      } else {
        toast(r.error || '操作失败', 'err');
      }
    } catch {
      toast('服务器连接失败', 'err');
    }
  };

  const doAdvance = async () => {
    const next = NEXT_LABELS[task.state] || '下一步';
    const comment = prompt(`⏩ 手动推进 ${task.id}\n当前: ${task.state} → 下一步: ${next}\n\n请输入说明（可留空）：`);
    if (comment === null) return;
    try {
      const r = await api.advanceState(task.id, comment || '');
      if (r.ok) {
        toast(`⏩ ${r.message}`, 'ok');
        loadAll();
        close();
      } else {
        toast(r.error || '推进失败', 'err');
      }
    } catch {
      toast('服务器连接失败', 'err');
    }
  };

  const doSchedAction = async (action: string) => {
    if (action === 'scan') {
      try {
        const r = await api.schedulerScan(180);
        if (r.ok) toast(`🔍 扫描完成：${r.count || 0} 个动作`, 'ok');
        else toast(r.error || '扫描失败', 'err');
        fetchSched();
      } catch {
        toast('服务器连接失败', 'err');
      }
      return;
    }
    const labels: Record<string, string> = { retry: '重试', escalate: '升级', rollback: '回滚' };
    const reason = prompt(`请输入${labels[action]}原因（可留空）：`);
    if (reason === null) return;
    const handlers: Record<string, (id: string, r: string) => Promise<{ ok: boolean; message?: string; error?: string }>> = {
      retry: api.schedulerRetry,
      escalate: api.schedulerEscalate,
      rollback: api.schedulerRollback,
    };
    try {
      const r = await handlers[action](task.id, reason);
      if (r.ok) toast(r.message || '操作成功', 'ok');
      else toast(r.error || '操作失败', 'err');
      fetchSched();
      loadAll();
    } catch {
      toast('服务器连接失败', 'err');
    }
  };

  const handleStop = () => {
    const reason = prompt('请输入叫停原因（可留空）：');
    if (reason === null) return;
    doTaskAction('stop', reason);
  };

  const handleCancel = () => {
    if (!confirm(`确定要取消 ${task.id} 吗？`)) return;
    const reason = prompt('请输入取消原因（可留空）：');
    if (reason === null) return;
    doTaskAction('cancel', reason);
  };

  // Scheduler state
  const sched = schedData?.scheduler;
  const stalledSec = schedData?.stalledSec || 0;
  const dInfo = dispatchInfo(sched);
  const lastDispatchAgent = agentLabel(sched?.lastDispatchAgent);
  const expectedAgent = agentLabel(schedData?.expectedAgent);
  const traceId = schedData?.traceId || activityData?.traceId || task.traceId || task.trace_id || '';
  const outbox = schedData?.outbox || activityData?.traceSummary?.outbox;
  const dispatchDiagnosis = schedData?.dispatchDiagnosis;
  const stageLine = activeStage
    ? `${activeStage.dept} · ${activeStage.action}`
    : stateLabel(task);

  return (
    <div className="modal-bg open" onClick={close}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={close} aria-label="关闭任务详情"><X size={18} /></button>
        <div className="modal-body">
          <div className="modal-id">{task.id}</div>
          <div className="modal-title">{task.title || '(无标题)'}</div>

          {/* Current Stage Banner */}
          {activeStage && (
            <div className="cur-stage">
              <div className="cs-icon">{activeStage.icon}</div>
              <div className="cs-info">
                <div className="cs-dept" style={{ color: deptColor(activeStage.dept) }}>{activeStage.dept}</div>
                <div className="cs-action">当前阶段：{activeStage.action}</div>
              </div>
              <div className="cs-side">
                <span className={`hb ${hb.status}`}>{hb.label}</span>
                <span className={`dispatch-pill ${dInfo.tone}`}>{dInfo.label}</span>
              </div>
            </div>
          )}

          {/* Pipeline */}
          <div className="m-pipe">
            {stages.map((s, i) => (
              <div className="mp-stage" key={s.key}>
                <div className={`mp-node ${s.status}`}>
                  {s.status === 'done' && <div className="mp-done-tick">✓</div>}
                  <div className="mp-icon">{s.icon}</div>
                  <div className="mp-dept" style={s.status === 'active' ? { color: 'var(--acc)' } : s.status === 'done' ? { color: 'var(--ok)' } : {}}>
                    {s.dept}
                  </div>
                  <div className="mp-action">{s.action}</div>
                </div>
                {i < stages.length - 1 && (
                  <div className="mp-arrow" style={s.status === 'done' ? { color: 'var(--ok)', opacity: 0.6 } : {}}>→</div>
                )}
              </div>
            ))}
          </div>

          {/* Action Buttons */}
          <div className="task-actions">
            {canStop && (
              <>
                <button className="btn-action btn-stop" onClick={handleStop}><Pause size={14} />叫停任务</button>
                <button className="btn-action btn-cancel" onClick={handleCancel}><Ban size={14} />取消任务</button>
              </>
            )}
            {canResume && (
              <button className="btn-action btn-resume" onClick={() => doTaskAction('resume', '恢复执行')}><Play size={14} />恢复执行</button>
            )}
            {['Review', 'Menxia'].includes(task.state) && (
              <>
                <button className="btn-action" style={{ background: '#2ecc8a22', color: '#2ecc8a', border: '1px solid #2ecc8a44' }} onClick={() => doReview('approve')}><CheckCircle2 size={14} />准奏</button>
                <button className="btn-action" style={{ background: '#ff527022', color: '#ff5270', border: '1px solid #ff527044' }} onClick={() => doReview('reject')}><XCircle size={14} />封驳</button>
              </>
            )}
            {['Pending', 'Taizi', 'Zhongshu', 'Menxia', 'Assigned', 'Doing', 'Review', 'Next'].includes(task.state) && (
              <button className="btn-action" style={{ background: '#7c5cfc18', color: '#7c5cfc', border: '1px solid #7c5cfc44' }} onClick={doAdvance}><SkipForward size={14} />推进到下一步</button>
            )}
          </div>

          {/* Runtime Summary */}
          <div className="run-section">
            <div className="run-head">
              <span className="run-title">运行摘要</span>
              <span className="run-sub">{sched?.enabled === false ? '调度已禁用' : `阈值 ${sched?.stallThresholdSec || 180}s`}</span>
            </div>
            <div className="run-grid">
              <div className="run-cell"><span>阶段</span><b>{stageLine}</b></div>
              <div className="run-cell"><span>派发</span><b className={`tone-${dInfo.tone}`}>{dInfo.label}</b></div>
              <div className="run-cell"><span>未推进</span><b>{fmtStalled(stalledSec)}</b></div>
              <div className="run-cell"><span>目标</span><b>{expectedAgent || lastDispatchAgent || task.org || '—'}</b></div>
              <div className="run-cell"><span>Trace</span><b className="mono">{shortTrace(traceId)}</b></div>
              <div className="run-cell"><span>队列</span><b className={outbox?.failed ? 'tone-err' : outbox?.pending || outbox?.running ? 'tone-warn' : 'tone-ok'}>{outboxLabel(outbox)}</b></div>
            </div>
            {dispatchDiagnosis && (
              <div className={`run-diagnosis ${dispatchDiagnosis.tone || 'idle'}`}>
                <b>{dispatchDiagnosis.label || '派发诊断'}</b>
                <span>{dispatchDiagnosis.detail || '等待调度信息'}</span>
                {dispatchDiagnosis.nextAction && <em>{dispatchDiagnosis.nextAction}</em>}
              </div>
            )}
            {sched && (
              <div className="run-line">
                {sched.lastProgressAt && <span>最近进展 {formatDashboardDateTime(sched.lastProgressAt)}</span>}
                {sched.lastDispatchAt && <span>最近派发 {formatDashboardDateTime(sched.lastDispatchAt)}</span>}
                {sched.lastDispatchError && <span className="run-error">派发错误：{sched.lastDispatchError}</span>}
                <span>重试 {sched.retryCount || 0}</span>
                <span>升级 {!sched.escalationLevel ? '无' : sched.escalationLevel === 1 ? '门下省' : '尚书省'}</span>
              </div>
            )}
            <div className="sched-actions compact">
              <button className="sched-btn" onClick={() => doSchedAction('scan')}><Search size={13} />立即扫描</button>
              <button className="sched-btn" onClick={() => doSchedAction('retry')}><RotateCcw size={13} />重试派发</button>
              <button className="sched-btn warn" onClick={() => doSchedAction('escalate')}><ArrowUpCircle size={13} />升级协调</button>
              <button className="sched-btn danger" onClick={() => doSchedAction('rollback')}><Undo2 size={13} />回滚</button>
            </div>
          </div>

          <CodingSessionSection
            data={codingData}
            onOpenSource={openSourcePreview}
            onCreatePatch={createPatchReview}
            onDecidePatch={decidePatchReview}
          />

          {sourcePreview && (
            <SourcePreviewPanel
              data={sourcePreview}
              onClose={() => setSourcePreview(null)}
              onOpenEditor={openSourceInEditor}
            />
          )}

          {/* Todo List */}
          {todoTotal > 0 && (
            <TodoSection todos={todos} todoDone={todoDone} todoTotal={todoTotal} />
          )}

          {/* Task Notes */}
          {(task.now || task.ac || task.block || task.eta || (task.review_round || 0) > 0) && (
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
          )}

          <TaskOutputSection group={activityData?.outputGroup} outputText={task.output} />

          {/* Live Activity */}
          <LiveActivitySection data={activityData} isDone={['Done', 'Cancelled'].includes(task.state)} logRef={logRef} />
        </div>
      </div>
    </div>
  );
}

function TodoSection({ todos, todoDone, todoTotal }: { todos: TodoItem[]; todoDone: number; todoTotal: number }) {
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

function TaskOutputSection({ group, outputText }: { group?: OutputGroup | null; outputText?: string }) {
  const note = group?.outputText || (outputText && outputText !== '-' ? outputText : '');
  const files = (group?.files || []).slice(0, 6);
  if (!note && !files.length) return null;

  return (
    <div className="task-output-section">
      <div className="task-output-head">
        <div>
          <div className="task-output-title"><Package size={14} />本任务产物</div>
          <div className="task-output-sub">
            {files.length ? `${group?.files?.length || files.length} 个文件` : '无文件'}{group?.updatedAt ? ` · ${formatDashboardDateTime(group.updatedAt)}` : ''}
          </div>
        </div>
        {group?.taskId && <span className="task-output-tag">{group.taskId}</span>}
      </div>
      {note && <div className="task-output-note">{note}</div>}
      {!!files.length && (
        <div className="task-output-list">
          {files.map((file) => (
            <div className="task-output-card" key={file.path}>
              <span className="task-output-icon"><FileText size={15} /></span>
              <span className="task-output-main">
                <b>{file.name}</b>
                <em>{file.kind} · {file.source} · {file.sizeLabel}</em>
                <code>{file.path}</code>
              </span>
              <span className="task-output-actions">
                <a href={api.outputFileUrl(file.path)} target="_blank" rel="noreferrer" title="打开产物">
                  <ExternalLink size={14} />
                </a>
                <a href={api.outputFileUrl(file.path, true)} title="下载产物">
                  <Download size={14} />
                </a>
              </span>
            </div>
          ))}
          {(group?.files?.length || 0) > files.length && (
            <div className="task-output-more">还有 {(group?.files?.length || 0) - files.length} 个文件，可到输出文件页查看</div>
          )}
        </div>
      )}
    </div>
  );
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

function CodingSessionSection({
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

  return (
    <div className="cockpit">
      <div className="cockpit-head">
        <div>
          <div className="cockpit-title">Coding Session 驾驶舱</div>
          <div className="cockpit-sub">
            {data.runtime || 'runtime'} · session <span className="mono">{shortTrace(data.sessionId)}</span>
          </div>
        </div>
        <div className={`cockpit-mode ${s.hasPatchReview ? 'ok' : 'warn'}`}>
          {s.hasPatchReview ? `Patch 审批已接入${s.pendingPatchCount ? ` · 待审 ${s.pendingPatchCount}` : ''}` : '尚未接入 Patch 审批'}
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
  return (
    <div className="patch-row">
      <div className="patch-main">
        <span className={`patch-status ${patchTone(review.status)}`}>{patchStatusLabel(review.status)}</span>
        <span className="patch-name">{fileCount} 文件{fileMix ? ` · ${fileMix}` : ''} · +{insertions} -{deletions}</span>
        <span className="patch-meta mono">{shortTrace(review.id)}{review.baseHead ? ` · ${review.baseHead}` : ''}</span>
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

function SourcePreviewPanel({
  data,
  onClose,
  onOpenEditor,
}: {
  data: SourceFileResult;
  onClose: () => void;
  onOpenEditor: (path: string, startLine?: number) => void;
}) {
  if (!data.ok) {
    return (
      <div className="source-preview">
        <div className="sp-head">
          <div className="sp-title">源码片段</div>
          <button className="sp-close" onClick={onClose}>关闭</button>
        </div>
        <div className="cockpit-empty">{data.error || '无法读取文件'}</div>
      </div>
    );
  }
  return (
    <div className="source-preview">
      <div className="sp-head">
        <div>
          <div className="sp-title">源码片段</div>
          <div className="sp-path">{data.path} · {data.viewStart}-{data.viewEnd}/{data.totalLines}</div>
        </div>
        <div className="sp-actions">
          <button
            className="sp-open"
            onClick={() => onOpenEditor(data.path, data.startLine || data.viewStart)}
            title="在本机编辑器打开"
          >
            <ExternalLink size={13} />
            打开编辑器
          </button>
          <button className="sp-close" onClick={onClose}>关闭</button>
        </div>
      </div>
      <pre className="sp-code">
        {data.lines.map((line) => (
          <div className={`sp-line${line.highlight ? ' hl' : ''}`} key={line.no}>
            <span className="sp-no">{line.no}</span>
            <code className="sp-text">{line.text || ' '}</code>
          </div>
        ))}
      </pre>
    </div>
  );
}

function LiveActivitySection({
  data,
  isDone,
  logRef,
}: {
  data: TaskActivityData | null;
  isDone: boolean;
  logRef: React.RefObject<HTMLDivElement | null>;
}) {
  if (!data) return null;

  const activity = data.activity || [];
  const isActive = (() => {
    if (!activity.length) return false;
    const last = activity[activity.length - 1];
    if (!last.at) return false;
    const ts = typeof last.at === 'number' ? last.at : new Date(last.at).getTime();
    return Date.now() - ts < 300000;
  })();

  const agentParts: string[] = [];
  if (data.agentLabel) agentParts.push(data.agentLabel);
  if (data.relatedAgents && data.relatedAgents.length > 1) agentParts.push(`${data.relatedAgents.length}个 Agent`);
  if (data.lastActive) agentParts.push(`最后活跃: ${formatDashboardDateTime(data.lastActive)}`);

  const phaseDurations = data.phaseDurations || [];
  const ts = data.todosSummary;
  const rs = data.resourceSummary;
  const evidence = data.stateEvidence;
  const evidenceTone = evidence?.confidence === 'high' || evidence?.confidence === 'complete'
    ? 'ok'
    : evidence?.confidence === 'medium'
      ? 'warn'
      : 'err';
  const currentPhase = phaseDurations.find((p) => p.ongoing) || phaseDurations[phaseDurations.length - 1];
  const timeline = compactActivity(activity);

  return (
    <div className="la-section">
      <div className="la-header">
        <span className="la-title">
          <span className={`la-dot${isActive ? '' : ' idle'}`} />
          {isDone ? '执行回顾' : '实时动态'}
        </span>
        <span className="la-agent">{agentParts.join(' · ') || '加载中...'}</span>
      </div>

      <div className="la-insights">
        {evidence && (
          <div className={`li-item ${evidenceTone}`}>
            <span>证据</span>
            <b>{evidence.label}</b>
            <em>{evidence.eventCount || 0} 条事件</em>
          </div>
        )}
        {currentPhase && (
          <div className="li-item">
            <span>当前耗时</span>
            <b>{currentPhase.phase}</b>
            <em>{currentPhase.durationText}{currentPhase.ongoing ? ' · 进行中' : ''}</em>
          </div>
        )}
        {ts && (
          <div className="li-item">
            <span>子任务</span>
            <b>{ts.percent}%</b>
            <em>{ts.completed}/{ts.total} 完成</em>
          </div>
        )}
        {rs && (rs.totalTokens || rs.totalCost || rs.totalElapsedSec) && (
          <div className="li-item">
            <span>资源</span>
            <b>{rs.totalTokens != null ? rs.totalTokens.toLocaleString() : '—'}</b>
            <em>{rs.totalCost != null ? `$${rs.totalCost.toFixed(4)}` : ''}{rs.totalElapsedSec != null ? ` · ${rs.totalElapsedSec}s` : ''}</em>
          </div>
        )}
        {data.totalDuration && (
          <div className="li-item">
            <span>总耗时</span>
            <b>{data.totalDuration}</b>
            <em>{phaseDurations.length} 个阶段</em>
          </div>
        )}
        {data.traceSummary?.traceId && (
          <div className={`li-item ${data.traceSummary.outbox?.failed ? 'err' : data.traceSummary.outbox?.pending || data.traceSummary.outbox?.running ? 'warn' : 'ok'}`}>
            <span>Trace</span>
            <b className="mono">{shortTrace(data.traceSummary.traceId)}</b>
            <em>{outboxLabel(data.traceSummary.outbox)}</em>
          </div>
        )}
        {data.activityWindow?.truncated && (
          <div className="li-item">
            <span>动态</span>
            <b>{data.activityWindow.returned || activity.length}/{data.activityWindow.total || activity.length}</b>
            <em>已折叠低信号记录</em>
          </div>
        )}
      </div>

      {ts && (
        <div className="la-progress-line">
          <div style={{ width: `${ts.total ? (ts.completed / ts.total) * 100 : 0}%` }} />
          <div className="active" style={{ width: `${ts.total ? (ts.inProgress / ts.total) * 100 : 0}%` }} />
        </div>
      )}

      <div className="la-log" ref={logRef as React.RefObject<HTMLDivElement>}>
        {timeline.length > 0 ? (
          timeline.map((a, i) => <ActivityEntryView key={`${activityKey(a)}-${i}`} entry={a} />)
        ) : (
          <div className="la-empty">
            {data.message || data.error || 'Agent 尚未上报进展（等待 Agent 调用 progress 命令）'}
          </div>
        )}
      </div>
    </div>
  );
}

function ActivityEntryView({ entry: a }: { entry: ActivityEntry }) {
  const time = fmtActivityTime(a.at);
  const agBadge = a.agent ? (
    <span style={{ fontSize: 9, color: 'var(--muted)', background: 'var(--panel)', padding: '1px 4px', borderRadius: 3, marginRight: 4 }}>
      {AGENT_LABELS[a.agent] || a.agent}
    </span>
  ) : null;

  if (a.kind === 'flow') {
    return (
      <div className="la-entry la-flow">
        <span className="la-icon">↳</span>
        <span className="la-body">
          <b>{a.from || '系统'}</b> → <b>{a.to || '未指定'}</b>
          {a.remark ? <span className="la-rem"> {a.remark}</span> : null}
        </span>
        <span className="la-time">{time}</span>
      </div>
    );
  }

  if (a.kind === 'progress') {
    return (
      <div className="la-entry la-assistant">
        <span className="la-icon">🔄</span>
        <span className="la-body">{agBadge}<b>当前进展：</b>{a.text}</span>
        <span className="la-time">{time}</span>
      </div>
    );
  }

  if (a.kind === 'todos') {
    const items = a.items || [];
    const diffMap = new Map<string, { type: string; from?: string; to?: string }>();
    if (a.diff) {
      (a.diff.changed || []).forEach((c) => diffMap.set(c.id, { type: 'changed', from: c.from, to: c.to }));
      (a.diff.added || []).forEach((c) => diffMap.set(c.id, { type: 'added' }));
    }
    return (
      <div className="la-entry" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 2 }}>
        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 2 }}>{agBadge}📝 执行计划</div>
        {items.map((td) => {
          const icon = td.status === 'completed' ? '✅' : td.status === 'in-progress' ? '🔄' : '⬜';
          const d = diffMap.get(String(td.id));
          const style: React.CSSProperties = td.status === 'completed'
            ? { opacity: 0.5, textDecoration: 'line-through' }
            : td.status === 'in-progress'
              ? { color: '#60a5fa', fontWeight: 'bold' }
              : {};
          return (
            <div key={td.id} style={style}>
              {icon} {td.title}
              {d && d.type === 'changed' && d.to === 'completed' && <span style={{ color: '#22c55e', fontSize: 9, marginLeft: 4 }}>✨刚完成</span>}
              {d && d.type === 'changed' && d.to !== 'completed' && <span style={{ color: '#f59e0b', fontSize: 9, marginLeft: 4 }}>↻{d.from}→{d.to}</span>}
              {d && d.type === 'added' && <span style={{ color: '#3b82f6', fontSize: 9, marginLeft: 4 }}>🆕新增</span>}
            </div>
          );
        })}
        {a.diff?.removed?.map((r) => (
          <div key={r.id} style={{ opacity: 0.4, textDecoration: 'line-through' }}>🗑 {r.title}</div>
        ))}
      </div>
    );
  }

  if (a.kind === 'assistant') {
    return (
      <>
        {a.thinking && (
          <div className="la-entry la-thinking">
            <span className="la-icon">⋯</span>
            <span className="la-body">
              {agBadge}
              <details className="la-evidence">
                <summary>行动证据：{a.thinking.length > 120 ? `${a.thinking.slice(0, 120)}…` : a.thinking}</summary>
                <div>{a.thinking}</div>
              </details>
            </span>
            <span className="la-time">{time}</span>
          </div>
        )}
        {a.tools?.map((tc, i) => (
          <div className="la-entry la-tool" key={i}>
            <span className="la-icon">🔧</span>
            <span className="la-body">{agBadge}<span className="la-tool-name">{tc.name}</span><span className="la-trunc">{tc.input_preview || ''}</span></span>
            <span className="la-time">{time}</span>
          </div>
        ))}
        {a.text && (
          <div className="la-entry la-assistant">
            <span className="la-icon">🤖</span>
            <span className="la-body">{agBadge}{a.text}</span>
            <span className="la-time">{time}</span>
          </div>
        )}
      </>
    );
  }

  if (a.kind === 'tool_result') {
    const ok = a.exitCode === 0 || a.exitCode === null || a.exitCode === undefined;
    return (
      <div className={`la-entry la-tool-result ${ok ? 'ok' : 'err'}`}>
        <span className="la-icon">{ok ? '✅' : '❌'}</span>
        <span className="la-body">{agBadge}<span className="la-tool-name">{a.tool || ''}</span>{a.output ? a.output.substring(0, 150) : ''}</span>
        <span className="la-time">{time}</span>
      </div>
    );
  }

  if (a.kind === 'user') {
    return (
      <div className="la-entry la-user">
        <span className="la-icon">📥</span>
        <span className="la-body">{agBadge}{a.text || ''}</span>
        <span className="la-time">{time}</span>
      </div>
    );
  }

  return (
    <div className="la-entry la-tool">
      <span className="la-icon">•</span>
      <span className="la-body">{agBadge}<span className="la-tool-name">{a.eventKind || a.kind}</span>{a.text || a.remark || ''}</span>
      <span className="la-time">{time}</span>
    </div>
  );
}
