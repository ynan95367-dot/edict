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

const LAYER_LABELS: Record<string, string> = {
  approval: '权限/审批',
  runtime: '运行时',
  model: '模型',
  queue: '执行队列',
  workspace: '工作区',
  scheduler: '调度器',
  agent: 'Agent',
  flow: '流程',
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
  const technicalMeta = [
    traceId ? `trace ${shortTrace(traceId)}` : '',
    runtimeSession?.sessionId ? `session ${shortTrace(runtimeSession.sessionId)}` : sessionLabel,
    outbox ? `请求 ${outboxLabel(outbox)}` : '',
  ].filter(Boolean).join(' · ') || '暂无技术细节';

  return (
    <div className="run-section">
      <div className="run-head">
        <div>
          <span className="run-title">卡点判断</span>
          <p>先给出当前结论、原因和可执行动作；工程细节默认收起。</p>
        </div>
        <span className="run-sub">{sched?.enabled === false ? '调度已禁用' : `阈值 ${sched?.stallThresholdSec || 180}s`}</span>
      </div>
      <div className={`run-diagnosis ${dispatchDiagnosis?.tone || 'idle'}`}>
        <span className="run-diagnosis-kicker">当前结论</span>
        <b>{dispatchDiagnosis?.label || '等待执行诊断'}</b>
        {dispatchDiagnosis?.blockingLayer && (
          <strong className="run-blocking-layer">卡在：{LAYER_LABELS[dispatchDiagnosis.blockingLayer] || dispatchDiagnosis.blockingLayer}</strong>
        )}
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

      <details className="run-technical-details">
        <summary>
          <span>技术细节</span>
          <small>{technicalMeta}</small>
        </summary>
        <div className="run-grid">
          <div className="run-cell"><span>阶段</span><b>{stageLine}</b></div>
          <div className="run-cell"><span>执行状态</span><b className={`tone-${dispatchInfo.tone}`}>{dispatchInfo.label}</b></div>
          <div className="run-cell"><span>静止时长</span><b>{fmtStalled(stalledSec)}</b></div>
          <div className="run-cell"><span>目标角色</span><b>{expectedAgent || lastDispatchAgent || taskOrg || '—'}</b></div>
          <div className="run-cell"><span>Trace</span><b className="mono">{shortTrace(traceId)}</b></div>
          <div className="run-cell"><span>Session</span><b className={`mono tone-${sessionTone}`}>{runtimeSession?.sessionId ? shortTrace(runtimeSession.sessionId) : '未绑定'}</b></div>
          <div className="run-cell"><span>绑定</span><b className={`tone-${sessionTone}`}>{sessionLabel}</b></div>
          <div className="run-cell"><span>执行请求</span><b className={outbox?.failed ? 'tone-err' : outbox?.pending || outbox?.running ? 'tone-warn' : 'tone-ok'}>{outboxLabel(outbox)}</b></div>
        </div>
        {sched && (
          <div className="run-line">
            {sched.lastProgressAt && <span>最近进展 {formatDashboardDateTime(sched.lastProgressAt)}</span>}
            {sched.lastDispatchAt && <span>最近执行请求 {formatDashboardDateTime(sched.lastDispatchAt)}</span>}
            {runtimeSession?.boundAt && <span>Session 绑定 {formatDashboardDateTime(runtimeSession.boundAt)}</span>}
            {sched.lastDispatchError && <span className="run-error">执行请求错误：{sched.lastDispatchError}</span>}
            <span>重试 {sched.retryCount || 0}</span>
            <span>升级 {!sched.escalationLevel ? '无' : sched.escalationLevel === 1 ? '门下省' : '尚书省'}</span>
          </div>
        )}
      </details>
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
            <button className="sched-btn" disabled={!!schedActionFeedback?.pending} onClick={() => onSchedAction('retry')}><RotateCcw size={13} />重新交办</button>
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
