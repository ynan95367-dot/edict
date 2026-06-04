import { useEffect, useState, useRef, useCallback } from 'react';
import { X } from 'lucide-react';
import { useStore, getPipeStatus, stateLabel } from '../store';
import { api } from '../api';
import { CodingSessionSection } from './task-modal/CodingSessionSection';
import { EvidenceChainSection } from './task-modal/EvidenceChainSection';
import { GovernanceMap } from './task-modal/GovernanceMap';
import { LiveActivitySection } from './task-modal/LiveActivitySection';
import { SchedulerDiagnosisPanel } from './task-modal/SchedulerDiagnosisPanel';
import { SourcePreviewPanel } from './task-modal/SourcePreviewPanel';
import { TaskActionsBar } from './task-modal/TaskActionsBar';
import { TaskNotesSection } from './task-modal/TaskNotesSection';
import { TaskOutputSection } from './task-modal/TaskOutputSection';
import { TodoSection } from './task-modal/TodoSection';
import {
  agentLabel,
  dispatchInfo,
  NEXT_LABELS,
  runtimeSessionLabel,
  runtimeSessionTone,
  SCHED_ACTION_LABELS,
  type SchedulerActionFeedback,
} from './task-modal/taskModalUtils';
import type {
  TaskActivityData,
  SchedulerStateData,
  CodingSessionData,
  TaskEvidenceData,
  SourceFileResult,
} from '../api';

export default function TaskModal() {
  const modalTaskId = useStore((s) => s.modalTaskId);
  const setModalTaskId = useStore((s) => s.setModalTaskId);
  const liveStatus = useStore((s) => s.liveStatus);
  const loadAll = useStore((s) => s.loadAll);
  const toast = useStore((s) => s.toast);

  const [activityData, setActivityData] = useState<TaskActivityData | null>(null);
  const [schedData, setSchedData] = useState<SchedulerStateData | null>(null);
  const [codingData, setCodingData] = useState<CodingSessionData | null>(null);
  const [evidenceData, setEvidenceData] = useState<TaskEvidenceData | null>(null);
  const [sourcePreview, setSourcePreview] = useState<SourceFileResult | null>(null);
  const [schedActionFeedback, setSchedActionFeedback] = useState<SchedulerActionFeedback | null>(null);
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

  const fetchEvidence = useCallback(async () => {
    if (!modalTaskId) return;
    try {
      const d = await api.taskEvidence(modalTaskId);
      setEvidenceData(d);
    } catch {
      setEvidenceData(null);
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
    setSourcePreview(null);
    setSchedActionFeedback(null);
    setEvidenceData(null);
  }, [modalTaskId]);

  useEffect(() => {
    if (!modalTaskId || !task) return;
    fetchActivity();
    fetchSched();
    fetchCodingSession();
    fetchEvidence();

    const isDone = ['Done', 'Cancelled'].includes(task.state);
    if (!isDone) {
      laTimerRef.current = setInterval(() => {
        fetchActivity();
        fetchSched();
        fetchCodingSession();
        fetchEvidence();
      }, 4000);
    }

    return () => {
      if (laTimerRef.current) {
        clearInterval(laTimerRef.current);
        laTimerRef.current = null;
      }
    };
  }, [modalTaskId, task?.state, fetchActivity, fetchSched, fetchCodingSession, fetchEvidence]);

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
  const isTerminalTask = ['Done', 'Cancelled'].includes(task.state);
  const canMutateSchedule = !isTerminalTask;
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

  const doSchedAction = async (action: string, reasonOverride?: string, source: 'manual' | 'diagnosis' = 'manual') => {
    if (!['scan', 'retry', 'escalate', 'rollback'].includes(action)) return;
    if (!canMutateSchedule && action !== 'scan') {
      toast('终态任务不再派发；需要继续时请先恢复任务', 'err');
      return;
    }
    const actionLabel = SCHED_ACTION_LABELS[action] || '处理';
    const labelPrefix = source === 'diagnosis' ? '按诊断建议' : '';
    if (action === 'scan') {
      setSchedActionFeedback({
        action,
        label: `${labelPrefix}${actionLabel}`,
        detail: '正在扫描运行证据和派发队列...',
        tone: 'warn',
        pending: true,
      });
      try {
        const r = await api.schedulerScan(180);
        if (r.ok) {
          const detail = `扫描完成，识别 ${r.count || 0} 个动作`;
          setSchedActionFeedback({ action, label: actionLabel, detail, tone: 'ok' });
          toast(detail, 'ok');
        } else {
          const detail = r.error || '扫描失败';
          setSchedActionFeedback({ action, label: `${actionLabel}失败`, detail, tone: 'err' });
          toast(detail, 'err');
        }
        fetchSched();
      } catch {
        setSchedActionFeedback({ action, label: `${actionLabel}失败`, detail: '服务器连接失败', tone: 'err' });
        toast('服务器连接失败', 'err');
      }
      return;
    }
    const labels: Record<string, string> = { retry: '重试', escalate: '升级', rollback: '回滚' };
    const reason = reasonOverride ?? prompt(`请输入${labels[action]}原因（可留空）：`);
    if (reason === null || reason === undefined) return;
    setSchedActionFeedback({
      action,
      label: `${labelPrefix}${actionLabel}`,
      detail: reason ? `原因：${reason}` : '正在提交调度动作...',
      tone: 'warn',
      pending: true,
    });
    const handlers: Record<string, (id: string, r: string) => Promise<{ ok: boolean; message?: string; error?: string }>> = {
      retry: api.schedulerRetry,
      escalate: api.schedulerEscalate,
      rollback: api.schedulerRollback,
    };
    try {
      const r = await handlers[action](task.id, reason);
      if (r.ok) {
        const detail = r.message || '操作成功';
        setSchedActionFeedback({ action, label: actionLabel, detail, tone: 'ok' });
        toast(detail, 'ok');
      } else {
        const detail = r.error || '操作失败';
        setSchedActionFeedback({ action, label: `${actionLabel}失败`, detail, tone: 'err' });
        toast(detail, 'err');
      }
      fetchSched();
      loadAll();
    } catch {
      setSchedActionFeedback({ action, label: `${actionLabel}失败`, detail: '服务器连接失败', tone: 'err' });
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
  const runtimeSession = schedData?.runtimeSession;
  const sessionTone = runtimeSessionTone(runtimeSession?.status);
  const sessionLabel = runtimeSessionLabel(runtimeSession?.status);
  const diagnosisAction = dispatchDiagnosis?.action;
  const canRunDiagnosisAction = !!diagnosisAction && ['scan', 'retry', 'escalate', 'rollback'].includes(diagnosisAction) && (canMutateSchedule || diagnosisAction === 'scan');
  const doneStages = stages.filter((s) => s.status === 'done').length;
  const completedStageCount = task.state === 'Done' ? stages.length : doneStages;
  const controlTone = dispatchDiagnosis?.tone || dInfo.tone;
  const stageLine = activeStage
    ? `${activeStage.dept} · ${activeStage.action}`
    : stateLabel(task);
  const controlDetail = dispatchDiagnosis?.detail || task.now || (activeStage ? `当前由${activeStage.dept}处理` : '等待任务状态刷新');
  const nextActionText = dispatchDiagnosis?.nextAction || (isTerminalTask ? '查看执行回顾或输出文件' : '等待 Agent 回写进展，必要时执行扫描');

  return (
    <div className="modal-bg open" onClick={close}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={close} aria-label="关闭任务详情"><X size={18} /></button>
        <div className="modal-body">
          <div className="modal-top">
            <div className="modal-meta">
              <span className="modal-id">{task.id}</span>
              <span className={`tag st-${task.state}`}>{stateLabel(task)}</span>
              <span className={`dispatch-pill ${dInfo.tone}`}>{dInfo.label}</span>
              <span className={`hb ${hb.status}`}>{hb.label}</span>
            </div>
            <div className="modal-title">{task.title || '(无标题)'}</div>
          </div>

          <div className={`task-control ${controlTone}`}>
            <div className="task-control-main">
              <div className="task-control-icon">{activeStage?.icon || '•'}</div>
              <div className="task-control-copy">
                <span>当前状态</span>
                <b>{stageLine}</b>
                <p>{controlDetail}</p>
              </div>
            </div>
            <div className="task-control-next">
              <span>建议动作</span>
              <b>{nextActionText}</b>
            </div>
          </div>

          <GovernanceMap stages={stages} completedStageCount={completedStageCount} />

          <TaskActionsBar
            taskState={task.state}
            canStop={canStop}
            canResume={canResume}
            onStop={handleStop}
            onCancel={handleCancel}
            onResume={() => doTaskAction('resume', '恢复执行')}
            onReview={doReview}
            onAdvance={doAdvance}
          />

          <SchedulerDiagnosisPanel
            sched={sched}
            stalledSec={stalledSec}
            dispatchInfo={dInfo}
            dispatchDiagnosis={dispatchDiagnosis}
            runtimeSession={runtimeSession}
            sessionTone={sessionTone}
            sessionLabel={sessionLabel}
            stageLine={stageLine}
            expectedAgent={expectedAgent}
            lastDispatchAgent={lastDispatchAgent}
            taskOrg={task.org}
            traceId={traceId}
            outbox={outbox}
            nextActionText={nextActionText}
            canMutateSchedule={canMutateSchedule}
            canRunDiagnosisAction={canRunDiagnosisAction}
            schedActionFeedback={schedActionFeedback}
            onSchedAction={doSchedAction}
          />

          <EvidenceChainSection data={evidenceData} />

          <TaskOutputSection group={activityData?.outputGroup} outputText={task.output} />

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

          <TaskNotesSection task={task} />

          {/* Live Activity */}
          <LiveActivitySection data={activityData} isDone={['Done', 'Cancelled'].includes(task.state)} logRef={logRef} />
        </div>
      </div>
    </div>
  );
}
