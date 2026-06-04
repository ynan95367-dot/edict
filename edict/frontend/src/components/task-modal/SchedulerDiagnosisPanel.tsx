import { ArrowUpCircle, RotateCcw, Search, Undo2 } from 'lucide-react';
import type { OutboxSummary, RuntimeSessionBinding, SchedulerInfo, SchedulerStateData } from '../../api';
import { formatDashboardDateTime } from '../../time';
import {
  fmtStalled,
  outboxLabel,
  shortTrace,
  type SchedulerActionFeedback,
} from './taskModalUtils';

type SchedulerDiagnosis = NonNullable<SchedulerStateData['dispatchDiagnosis']>;
type DispatchInfo = { label: string; tone: 'ok' | 'warn' | 'err' | 'idle' | string };

type SchedulerDiagnosisPanelProps = {
  sched?: SchedulerInfo;
  stalledSec: number;
  dispatchInfo: DispatchInfo;
  dispatchDiagnosis?: SchedulerDiagnosis;
  runtimeSession?: RuntimeSessionBinding;
  sessionTone: 'ok' | 'warn' | 'err' | 'idle';
  sessionLabel: string;
  stageLine: string;
  expectedAgent: string;
  lastDispatchAgent: string;
  taskOrg: string;
  traceId: string;
  outbox?: OutboxSummary;
  nextActionText: string;
  canMutateSchedule: boolean;
  canRunDiagnosisAction: boolean;
  schedActionFeedback: SchedulerActionFeedback | null;
  onSchedAction: (action: string, reasonOverride?: string, source?: 'manual' | 'diagnosis') => void;
};

export function SchedulerDiagnosisPanel({
  sched,
  stalledSec,
  dispatchInfo,
  dispatchDiagnosis,
  runtimeSession,
  sessionTone,
  sessionLabel,
  stageLine,
  expectedAgent,
  lastDispatchAgent,
  taskOrg,
  traceId,
  outbox,
  nextActionText,
  canMutateSchedule,
  canRunDiagnosisAction,
  schedActionFeedback,
  onSchedAction,
}: SchedulerDiagnosisPanelProps) {
  const diagnosisAction = dispatchDiagnosis?.action;

  return (
    <div className="run-section">
      <div className="run-head">
        <div>
          <span className="run-title">调度诊断</span>
          <p>把状态、派发、队列和下一步动作合并成一个可执行判断。</p>
        </div>
        <span className="run-sub">{sched?.enabled === false ? '调度已禁用' : `阈值 ${sched?.stallThresholdSec || 180}s`}</span>
      </div>
      <div className="run-layout">
        <div className={`run-diagnosis ${dispatchDiagnosis?.tone || 'idle'}`}>
          <span className="run-diagnosis-kicker">系统判断</span>
          <b>{dispatchDiagnosis?.label || '等待派发诊断'}</b>
          <span>{dispatchDiagnosis?.detail || '调度信息读取中，尚无明确异常。'}</span>
          <em>{dispatchDiagnosis?.nextAction || nextActionText}</em>
          {canRunDiagnosisAction && (
            <button
              type="button"
              disabled={!!schedActionFeedback?.pending}
              onClick={() => onSchedAction(diagnosisAction || '', dispatchDiagnosis?.actionReason || dispatchDiagnosis?.detail || dispatchDiagnosis?.label || '', 'diagnosis')}
            >
              {dispatchDiagnosis?.actionLabel || '处理'}
            </button>
          )}
        </div>
        <div className="run-grid">
          <div className="run-cell"><span>阶段</span><b>{stageLine}</b></div>
          <div className="run-cell"><span>派发</span><b className={`tone-${dispatchInfo.tone}`}>{dispatchInfo.label}</b></div>
          <div className="run-cell"><span>未推进</span><b>{fmtStalled(stalledSec)}</b></div>
          <div className="run-cell"><span>目标</span><b>{expectedAgent || lastDispatchAgent || taskOrg || '—'}</b></div>
          <div className="run-cell"><span>Trace</span><b className="mono">{shortTrace(traceId)}</b></div>
          <div className="run-cell"><span>Session</span><b className={`mono tone-${sessionTone}`}>{runtimeSession?.sessionId ? shortTrace(runtimeSession.sessionId) : '未绑定'}</b></div>
          <div className="run-cell"><span>绑定</span><b className={`tone-${sessionTone}`}>{sessionLabel}</b></div>
          <div className="run-cell"><span>队列</span><b className={outbox?.failed ? 'tone-err' : outbox?.pending || outbox?.running ? 'tone-warn' : 'tone-ok'}>{outboxLabel(outbox)}</b></div>
        </div>
      </div>
      {sched && (
        <div className="run-line">
          {sched.lastProgressAt && <span>最近进展 {formatDashboardDateTime(sched.lastProgressAt)}</span>}
          {sched.lastDispatchAt && <span>最近派发 {formatDashboardDateTime(sched.lastDispatchAt)}</span>}
          {runtimeSession?.boundAt && <span>Session 绑定 {formatDashboardDateTime(runtimeSession.boundAt)}</span>}
          {sched.lastDispatchError && <span className="run-error">派发错误：{sched.lastDispatchError}</span>}
          <span>重试 {sched.retryCount || 0}</span>
          <span>升级 {!sched.escalationLevel ? '无' : sched.escalationLevel === 1 ? '门下省' : '尚书省'}</span>
        </div>
      )}
      {schedActionFeedback && (
        <div className={`run-action-feedback ${schedActionFeedback.tone}${schedActionFeedback.pending ? ' pending' : ''}`} role="status">
          <b>{schedActionFeedback.label}</b>
          <span>{schedActionFeedback.detail}</span>
        </div>
      )}
      <div className="sched-actions compact">
        <button className="sched-btn" disabled={!!schedActionFeedback?.pending} onClick={() => onSchedAction('scan')}><Search size={13} />立即扫描</button>
        {canMutateSchedule ? (
          <>
            <button className="sched-btn" disabled={!!schedActionFeedback?.pending} onClick={() => onSchedAction('retry')}><RotateCcw size={13} />重试派发</button>
            <button className="sched-btn warn" disabled={!!schedActionFeedback?.pending} onClick={() => onSchedAction('escalate')}><ArrowUpCircle size={13} />升级协调</button>
            <button className="sched-btn danger" disabled={!!schedActionFeedback?.pending} onClick={() => onSchedAction('rollback')}><Undo2 size={13} />回滚</button>
          </>
        ) : (
          <span className="sched-terminal-note">终态任务仅保留证据扫描</span>
        )}
      </div>
    </div>
  );
}
