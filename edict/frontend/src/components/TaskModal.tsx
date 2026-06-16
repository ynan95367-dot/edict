import { useEffect, useState, useRef, useCallback } from 'react';
import { CheckCircle2, ShieldAlert, X, XCircle } from 'lucide-react';
import { useStore, getPipeStatus, stateLabel } from '../store';
import { api } from '../api';
import { CodingSessionSection } from './task-modal/CodingSessionSection';
import { EvidenceChainSection } from './task-modal/EvidenceChainSection';
import { ExecutionNarrativePanel } from './task-modal/ExecutionNarrativePanel';
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
  outboxLabel,
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
  PolicyGate,
  ToolPolicy,
} from '../api';

type ApprovalView = {
  visible: boolean;
  tone: 'warn' | 'err' | 'ok';
  title: string;
  subject: string;
  reason: string;
  approveEffect: string;
  rejectEffect: string;
  permissions: string[];
  mode: 'policy' | 'menxia' | 'review' | 'confirm';
};

const GATE_RELEASED = new Set(['approved', 'released', 'bypassed']);
const DIAGNOSTIC_LAYER_LABELS: Record<string, string> = {
  approval: '审批',
  runtime: '运行时',
  model: '模型',
  queue: '执行请求',
  workspace: '工作区',
  scheduler: '调度器',
  agent: 'Agent 回写',
  flow: '流程',
};

function approvalIsWaiting(gate?: PolicyGate | null): boolean {
  if (!gate) return false;
  const decision = String(gate.decision || '');
  const status = String(gate.status || '');
  return decision !== 'auto_dispatch' && !GATE_RELEASED.has(status);
}

function approvalViewForTask(args: {
  taskState: string;
  taskTitle: string;
  taskNow?: string;
  taskOutput?: string;
  pendingConfirm?: {
    target_state?: string;
    requested_by?: string;
    requested_at?: string;
    confirm_by?: string;
  } | null;
  expectedAgent: string;
  dispatchStatus?: string;
  dispatchError?: string;
  policyGate?: PolicyGate | null;
  toolPolicy?: ToolPolicy | null;
}): ApprovalView | null {
  const policyWaiting = approvalIsWaiting(args.policyGate) || args.dispatchStatus === 'policy-held';
  const permissions = [
    ...((args.policyGate?.permissionLabels || []) as string[]),
    ...((args.toolPolicy?.permissionLabels || []) as string[]),
    ...((args.toolPolicy?.permissions || []) as string[]),
  ].filter(Boolean).slice(0, 6);
  const reason = args.policyGate?.reason || args.toolPolicy?.approvalReason || args.dispatchError || '';
  if (policyWaiting) {
    return {
      visible: true,
      tone: 'warn',
      mode: 'policy',
      title: '需要你确认执行权限',
      subject: args.taskTitle || '当前任务',
      reason: reason || '这个任务会触发高风险能力或命令执行，系统已暂停自动交办。',
      approveEffect: `准奏后：释放权限闸门，并继续交给 ${args.expectedAgent || '目标官署'} 执行。`,
      rejectEffect: '封驳后：退回中书省修订，当前执行请求不会继续。',
      permissions,
    };
  }
  if (args.taskState === 'PendingConfirm') {
    const subject = [args.taskTitle || '当前任务', args.taskOutput || ''].filter(Boolean).join(' · ');
    const targetState = args.pendingConfirm?.target_state || '';
    const targetLabel = targetState ? (NEXT_LABELS[targetState] || targetState) : '待确认目标';
    return {
      visible: true,
      tone: 'warn',
      mode: 'confirm',
      title: '需要你最终确认结果',
      subject,
      reason: reason || args.taskNow || `执行方已提交收口请求，等待你确认是否进入 ${targetLabel}。`,
      approveEffect: `准奏后：进入 ${targetLabel}${targetState === 'Done' ? '，任务收口完成' : ''}。`,
      rejectEffect: '封驳后：退回尚书省复审，继续补充或修正结果。',
      permissions,
    };
  }
  if (args.taskState === 'Menxia') {
    return {
      visible: true,
      tone: 'warn',
      mode: 'menxia',
      title: '需要你审议方案',
      subject: args.taskTitle || '中书省方案',
      reason: reason || '门下省正在等你判断方案是否可以进入执行。',
      approveEffect: '准奏后：交给尚书省安排执行。',
      rejectEffect: '封驳后：退回中书省修订方案。',
      permissions,
    };
  }
  if (args.taskState === 'Review') {
    return {
      visible: true,
      tone: 'warn',
      mode: 'review',
      title: '需要你审查结果',
      subject: args.taskTitle || '执行结果',
      reason: reason || '任务已进入回奏审查，等你确认是否收口。',
      approveEffect: '准奏后：任务进入完成状态。',
      rejectEffect: '封驳后：退回中书省重新修订。',
      permissions,
    };
  }
  return null;
}

function ImperialApprovalPanel({
  approval,
  onReview,
}: {
  approval: ApprovalView;
  onReview: (action: 'approve' | 'reject') => void;
}) {
  return (
    <section className={`imperial-approval ${approval.tone}`}>
      <div className="ia-icon"><ShieldAlert size={20} /></div>
      <div className="ia-main">
        <div className="ia-kicker">皇上待决</div>
        <h3>{approval.title}</h3>
        <p>{approval.reason}</p>
        <div className="ia-subject">
          <span>审批对象</span>
          <b>{approval.subject}</b>
        </div>
        {approval.permissions.length > 0 && (
          <div className="ia-permissions">
            {approval.permissions.map((item) => <span key={item}>{item}</span>)}
          </div>
        )}
        <div className="ia-effects">
          <div><span>准奏</span><b>{approval.approveEffect}</b></div>
          <div><span>封驳</span><b>{approval.rejectEffect}</b></div>
        </div>
      </div>
      <div className="ia-actions">
        <button className="ia-approve" onClick={() => onReview('approve')}><CheckCircle2 size={15} />准奏</button>
        <button className="ia-reject" onClick={() => onReview('reject')}><XCircle size={15} />封驳</button>
      </div>
    </section>
  );
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
  const [evidenceData, setEvidenceData] = useState<TaskEvidenceData | null>(null);
  const [sourcePreview, setSourcePreview] = useState<SourceFileResult | null>(null);
  const [schedActionFeedback, setSchedActionFeedback] = useState<SchedulerActionFeedback | null>(null);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const laTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const modalTaskIdRef = useRef<string | null>(null);
  const activityInFlightRef = useRef(false);
  const schedInFlightRef = useRef(false);
  const codingInFlightRef = useRef(false);
  const evidenceInFlightRef = useRef(false);

  const task = liveStatus?.tasks?.find((t) => t.id === modalTaskId) || null;

  useEffect(() => {
    modalTaskIdRef.current = modalTaskId;
  }, [modalTaskId]);

  const fetchActivity = useCallback(async () => {
    if (!modalTaskId || activityInFlightRef.current) return;
    const taskId = modalTaskId;
    activityInFlightRef.current = true;
    try {
      const d = await api.taskActivity(taskId);
      if (modalTaskIdRef.current === taskId) setActivityData(d);
    } catch {
      // Keep the last good snapshot to avoid UI sections blinking during transient backend work.
    } finally {
      activityInFlightRef.current = false;
    }
  }, [modalTaskId]);

  const fetchSched = useCallback(async () => {
    if (!modalTaskId || schedInFlightRef.current) return;
    const taskId = modalTaskId;
    schedInFlightRef.current = true;
    try {
      const d = await api.schedulerState(taskId);
      if (modalTaskIdRef.current === taskId) setSchedData(d);
    } catch {
      // Preserve last good data; the collapsed diagnostics summary should not disappear.
    } finally {
      schedInFlightRef.current = false;
    }
  }, [modalTaskId]);

  const fetchCodingSession = useCallback(async () => {
    if (!modalTaskId || codingInFlightRef.current) return;
    const taskId = modalTaskId;
    codingInFlightRef.current = true;
    try {
      const d = await api.codingSession(taskId);
      if (modalTaskIdRef.current === taskId) setCodingData(d);
    } catch {
      // Preserve last good data; this endpoint may touch git/worktree state.
    } finally {
      codingInFlightRef.current = false;
    }
  }, [modalTaskId]);

  const fetchEvidence = useCallback(async () => {
    if (!modalTaskId || evidenceInFlightRef.current) return;
    const taskId = modalTaskId;
    evidenceInFlightRef.current = true;
    try {
      const d = await api.taskEvidence(taskId);
      if (modalTaskIdRef.current === taskId) setEvidenceData(d);
    } catch {
      // Preserve last good data; avoid section mount/unmount flicker.
    } finally {
      evidenceInFlightRef.current = false;
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
    setActivityData(null);
    setSchedData(null);
    setCodingData(null);
    setEvidenceData(null);
    setSourcePreview(null);
    setSchedActionFeedback(null);
    setDiagnosticsOpen(false);
    setActivityOpen(false);
    activityInFlightRef.current = false;
    schedInFlightRef.current = false;
    codingInFlightRef.current = false;
    evidenceInFlightRef.current = false;
  }, [modalTaskId]);

  useEffect(() => {
    if (!modalTaskId || !task) return;
    fetchActivity();
    fetchSched();
    if (diagnosticsOpen) {
      fetchCodingSession();
      fetchEvidence();
    }

    const isDone = ['Done', 'Cancelled'].includes(task.state);
    if (!isDone) {
      laTimerRef.current = setInterval(() => {
        fetchActivity();
        fetchSched();
        if (diagnosticsOpen) {
          fetchCodingSession();
          fetchEvidence();
        }
      }, diagnosticsOpen ? 6000 : 8000);
    }

    return () => {
      if (laTimerRef.current) {
        clearInterval(laTimerRef.current);
        laTimerRef.current = null;
      }
    };
  }, [modalTaskId, task?.state, diagnosticsOpen, fetchActivity, fetchSched, fetchCodingSession, fetchEvidence]);

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
    const gate = task.runSpec?.policyGate;
    const approvalReason = gate?.reason || task.now || schedData?.dispatchDiagnosis?.detail || '请根据当前任务状态判断是否继续。';
    const approvalSubject = task.title || task.id;
    const comment = prompt(
      `${labels[action]} ${task.id}\n` +
      `审批对象：${approvalSubject}\n` +
      `当前阶段：${stateLabel(task)}\n` +
      `系统提示：${approvalReason}\n\n` +
      '请输入批注（可留空）：'
    );
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
      toast('终态任务不再交办执行；需要继续时请先恢复任务', 'err');
      return;
    }
    const actionLabel = SCHED_ACTION_LABELS[action] || '处理';
    const labelPrefix = source === 'diagnosis' ? '按诊断建议' : '';
    if (action === 'scan') {
      setSchedActionFeedback({
        action,
        label: `${labelPrefix}${actionLabel}`,
        detail: '正在扫描运行证据和执行队列...',
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
  const approvalView = approvalViewForTask({
    taskState: task.state,
    taskTitle: task.title,
    taskNow: task.now,
    taskOutput: task.output,
    pendingConfirm: task.pending_confirm || null,
    expectedAgent,
    dispatchStatus: sched?.lastDispatchStatus,
    dispatchError: sched?.lastDispatchError,
    policyGate: codingData?.runSpec?.policyGate || task.runSpec?.policyGate || null,
    toolPolicy: codingData?.runSpec?.toolPolicy || task.runSpec?.toolPolicy || null,
  });
  const nextActionText = dispatchDiagnosis?.nextAction || (isTerminalTask ? '查看执行回顾或输出文件' : '等待 Agent 回写进展，必要时执行扫描');
  const primaryNextActionText = approvalView?.visible ? '先处理皇上待决，再继续交办执行' : nextActionText;
  const doneStages = stages.filter((s) => s.status === 'done').length;
  const completedStageCount = task.state === 'Done' ? stages.length : doneStages;
  const controlTone = dispatchDiagnosis?.tone || dInfo.tone;
  const stageLine = activeStage
    ? `${activeStage.dept} · ${activeStage.action}`
    : stateLabel(task);
  const controlDetail = dispatchDiagnosis?.detail || task.now || (activeStage ? `当前由${activeStage.dept}处理` : '等待任务状态刷新');
  const evidenceHealth = evidenceData?.health?.status || '';
  const diagnosticTone = evidenceHealth === 'err' || dispatchDiagnosis?.tone === 'err' || dInfo.tone === 'err'
    ? 'err'
    : evidenceHealth === 'warn' || dispatchDiagnosis?.tone === 'warn' || dInfo.tone === 'warn'
      ? 'warn'
      : evidenceHealth === 'ok' || dispatchDiagnosis?.tone === 'ok' || dInfo.tone === 'ok'
        ? 'ok'
        : 'idle';
  const graphSummary = codingData?.runSpec?.runGraph?.summary;
  const blockingLayerLabel = dispatchDiagnosis?.blockingLayer
    ? DIAGNOSTIC_LAYER_LABELS[dispatchDiagnosis.blockingLayer] || dispatchDiagnosis.blockingLayer
    : '';
  const diagnosticMeta = [
    blockingLayerLabel ? `卡点 ${blockingLayerLabel}` : '',
    dispatchDiagnosis?.actionLabel ? `建议 ${dispatchDiagnosis.actionLabel}` : '',
    outbox && (outbox.running || outbox.pending || outbox.failed) ? `执行请求 ${outboxLabel(outbox)}` : '',
    graphSummary ? `执行图 ${graphSummary.nodeCount || 0} 节点` : '',
  ].filter(Boolean).join(' · ') || '无当前阻塞';
  const diagnosticLabel = dispatchDiagnosis?.label || evidenceData?.health?.label || dInfo.label || '运行证据';
  const activityCount = activityData?.activity?.length || 0;
  const activityLabel = isTerminalTask ? '执行回顾' : '实时动态';
  const activityMeta = [
    activityData?.agentLabel || '',
    activityData?.lastActive ? `最后活跃 ${activityData.lastActive.replace('T', ' ').slice(0, 16)}` : '',
    activityCount ? `${activityCount} 条记录` : '',
  ].filter(Boolean).join(' · ') || '等待 Agent 上报';

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

          <ExecutionNarrativePanel
            task={task}
            stageLine={stageLine}
            controlTone={controlTone}
            currentDetail={controlDetail}
            nextActionText={primaryNextActionText}
            approval={approvalView}
            sched={sched}
            stalledSec={stalledSec}
            dispatchDiagnosis={dispatchDiagnosis}
            expectedAgent={expectedAgent}
            traceId={traceId}
            outbox={outbox}
            activityData={activityData}
            evidenceData={evidenceData}
          />

          {approvalView?.visible && (
            <ImperialApprovalPanel approval={approvalView} onReview={doReview} />
          )}

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
            showReviewActions={!approvalView?.visible}
          />

          <TaskOutputSection group={activityData?.outputGroup} outputText={task.output} />

          {todoTotal > 0 && (
            <TodoSection todos={todos} todoDone={todoDone} todoTotal={todoTotal} />
          )}

          <TaskNotesSection task={task} />

          <div className="diagnostic-shell activity-shell">
            <button
              type="button"
              className="diagnostic-toggle"
              aria-expanded={activityOpen}
              onClick={() => setActivityOpen((value) => !value)}
            >
              <span>{activityLabel}</span>
              <b>{activityData?.stateEvidence?.label || task.now || '等待进展'}</b>
              <small>{activityMeta}</small>
              <em>{activityOpen ? '收起' : '展开'}</em>
            </button>
            {activityOpen && (
              <div className="diagnostic-stack">
                <LiveActivitySection data={activityData} isDone={['Done', 'Cancelled'].includes(task.state)} logRef={logRef} />
              </div>
            )}
          </div>

          <div className={`diagnostic-shell ${diagnosticTone}`}>
            <button
              type="button"
              className="diagnostic-toggle"
              aria-expanded={diagnosticsOpen}
              onClick={() => setDiagnosticsOpen((value) => !value)}
            >
              <span>更多证据</span>
              <b>{diagnosticLabel}</b>
              <small>{diagnosticMeta}</small>
              <em>{diagnosticsOpen ? '收起' : '展开'}</em>
            </button>
            {diagnosticsOpen && (
              <div className="diagnostic-stack">
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

                <CodingSessionSection
                  data={codingData}
                  onOpenSource={openSourcePreview}
                  onCreatePatch={createPatchReview}
                  onDecidePatch={decidePatchReview}
                />
              </div>
            )}
          </div>

          {sourcePreview && (
            <SourcePreviewPanel
              data={sourcePreview}
              onClose={() => setSourcePreview(null)}
              onOpenEditor={openSourceInEditor}
            />
          )}
        </div>
      </div>
    </div>
  );
}
