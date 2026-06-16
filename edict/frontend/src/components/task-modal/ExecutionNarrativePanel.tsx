import { Activity, AlertTriangle, CheckCircle2, CircleDashed, GitBranch, ListChecks } from 'lucide-react';
import type {
  ActivityEntry,
  SchedulerInfo,
  SchedulerStateData,
  Task,
  TaskActivityData,
  TaskEvidenceData,
} from '../../api';
import { formatDashboardDateTime, formatDashboardTime } from '../../time';
import { stateLabel } from '../../store';
import { agentLabel, fmtStalled, outboxLabel, shortTrace } from './taskModalUtils';

type ApprovalSummary = {
  visible?: boolean;
  title?: string;
  reason?: string;
  approveEffect?: string;
  rejectEffect?: string;
} | null;

type DispatchDiagnosis = NonNullable<SchedulerStateData['dispatchDiagnosis']>;

type ExecutionNarrativePanelProps = {
  task: Task;
  stageLine: string;
  controlTone: string;
  currentDetail: string;
  nextActionText: string;
  approval?: ApprovalSummary;
  sched?: SchedulerInfo;
  stalledSec: number;
  dispatchDiagnosis?: DispatchDiagnosis;
  expectedAgent: string;
  traceId: string;
  outbox?: SchedulerStateData['outbox'];
  activityData: TaskActivityData | null;
  evidenceData: TaskEvidenceData | null;
};

const LAYER_LABELS: Record<string, string> = {
  approval: '权限审批',
  runtime: '运行时连接',
  model: '模型可用性',
  queue: '执行队列',
  workspace: '工作区',
  scheduler: '调度器',
  agent: 'Agent 回写',
  flow: '流程状态',
};

function toneLabel(tone?: string): string {
  if (tone === 'err') return '需要处理';
  if (tone === 'warn') return '需要观察';
  if (tone === 'ok') return '可推进';
  return '待判断';
}

function compactText(value?: string, fallback = '已记录'): string {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  if (!text) return fallback;
  return text.length > 150 ? `${text.slice(0, 150)}...` : text;
}

function actionLabel(entry: ActivityEntry): { title: string; detail: string; tone: string; meta: string } {
  const meta = [
    entry.agent ? agentLabel(entry.agent) : '',
    entry.at ? formatDashboardTime(entry.at, { showSeconds: true }) : '',
  ].filter(Boolean).join(' · ');
  if (entry.kind === 'flow') {
    return {
      title: `${entry.from || '系统'} -> ${entry.to || '下一阶段'}`,
      detail: compactText(entry.remark, '流程节点已更新'),
      tone: 'ok',
      meta,
    };
  }
  if (entry.kind === 'progress') {
    return { title: '更新进展', detail: compactText(entry.text), tone: 'ok', meta };
  }
  if (entry.kind === 'todos') {
    const items = entry.items || [];
    const active = items.find((item) => item.status === 'in-progress');
    return {
      title: '更新执行计划',
      detail: active ? `正在处理：${active.title}` : `记录 ${items.length} 个子任务`,
      tone: 'ok',
      meta,
    };
  }
  if (entry.kind === 'tool_result') {
    const ok = entry.exitCode === 0 || entry.exitCode === null || entry.exitCode === undefined;
    return {
      title: ok ? '命令完成' : '命令失败',
      detail: compactText(entry.output, entry.tool || '工具返回结果'),
      tone: ok ? 'ok' : 'err',
      meta,
    };
  }
  if (entry.kind === 'assistant' && entry.tools?.length) {
    return {
      title: `调用工具：${entry.tools.map((tool) => tool.name).slice(0, 2).join(' / ')}`,
      detail: compactText(entry.tools[0]?.input_preview || entry.text, '准备读取或修改上下文'),
      tone: 'warn',
      meta,
    };
  }
  if (entry.kind === 'assistant' && entry.thinking) {
    return { title: '形成判断', detail: compactText(entry.thinking), tone: 'warn', meta };
  }
  if (entry.kind === 'user') {
    return { title: '收到任务', detail: compactText(entry.text), tone: 'ok', meta };
  }
  return {
    title: entry.eventKind || entry.kind || '运行事件',
    detail: compactText(entry.text || entry.remark || entry.output),
    tone: entry.exitCode && entry.exitCode !== 0 ? 'err' : 'idle',
    meta,
  };
}

function recentActions(activityData: TaskActivityData | null, task: Task) {
  const raw = activityData?.activity?.length ? activityData.activity : task.activity || [];
  return raw
    .slice()
    .reverse()
    .filter((entry) => ['flow', 'progress', 'todos', 'assistant', 'tool_result', 'user'].includes(entry.kind))
    .slice(0, 5)
    .map(actionLabel);
}

function buildDecisionSteps(args: {
  approval?: ApprovalSummary;
  dispatchDiagnosis?: DispatchDiagnosis;
  sched?: SchedulerInfo;
  stalledSec: number;
  nextActionText: string;
  expectedAgent: string;
  task: Task;
}) {
  const steps: Array<{ label: string; detail: string; tone: string }> = [];
  if (args.approval?.visible) {
    steps.push({
      label: '先处理审批',
      detail: args.approval.reason || args.approval.title || '当前流程等待人工确认。',
      tone: 'warn',
    });
    steps.push({
      label: '若准奏',
      detail: args.approval.approveEffect || `继续交给 ${args.expectedAgent || '目标 Agent'} 执行。`,
      tone: 'ok',
    });
    steps.push({
      label: '若封驳',
      detail: args.approval.rejectEffect || '退回前序阶段修订，当前执行请求停止推进。',
      tone: 'err',
    });
    return steps;
  }
  const layer = args.dispatchDiagnosis?.blockingLayer || '';
  if (layer) {
    steps.push({
      label: `先看 ${LAYER_LABELS[layer] || layer}`,
      detail: args.dispatchDiagnosis?.detail || '系统已定位到当前最可能的阻塞层。',
      tone: args.dispatchDiagnosis?.tone || 'warn',
    });
  }
  if (args.dispatchDiagnosis?.action && args.dispatchDiagnosis.action !== 'none') {
    steps.push({
      label: '可执行动作',
      detail: args.dispatchDiagnosis.actionLabel || args.nextActionText,
      tone: args.dispatchDiagnosis?.tone === 'err' ? 'err' : 'warn',
    });
  }
  if (!steps.length) {
    steps.push({
      label: args.task.state === 'Done' ? '已收口' : '继续推进',
      detail: args.nextActionText,
      tone: args.task.state === 'Done' ? 'ok' : 'warn',
    });
  }
  if (args.sched?.stallThresholdSec && args.task.state !== 'Done') {
    const left = Math.max(0, (args.sched.stallThresholdSec || 0) - args.stalledSec);
    steps.push({
      label: left > 0 ? '若超时' : '已到阈值',
      detail: left > 0
        ? `若 ${fmtStalled(left)} 内没有新进展，再扫描证据或重新交办。`
        : '已超过调度阈值，可以扫描证据、重新交办或升级协调。',
      tone: left > 0 ? 'idle' : 'warn',
    });
  }
  return steps.slice(0, 4);
}

function failureExplanation(args: {
  dispatchDiagnosis?: DispatchDiagnosis;
  sched?: SchedulerInfo;
  evidenceData: TaskEvidenceData | null;
  outbox?: SchedulerStateData['outbox'];
}) {
  const error = args.sched?.lastDispatchError || args.evidenceData?.health?.detail || '';
  if (args.outbox?.failed) {
    return {
      title: '有执行请求失败',
      detail: args.dispatchDiagnosis?.detail || error || '队列里还有失败项，需要先重试或归档。',
      next: args.dispatchDiagnosis?.nextAction || '先查看失败记录，再决定重试、归档或升级协调。',
      tone: 'err',
    };
  }
  if (args.dispatchDiagnosis?.tone === 'err') {
    return {
      title: args.dispatchDiagnosis.label || '当前阻塞需要处理',
      detail: args.dispatchDiagnosis.detail || error || '系统已定位到异常，但缺少更细证据。',
      next: args.dispatchDiagnosis.nextAction || '按诊断建议处理后再观察队列。',
      tone: 'err',
    };
  }
  if (error) {
    return {
      title: '最近一次错误已记录',
      detail: error,
      next: args.dispatchDiagnosis?.nextAction || '如果再次出现，先检查 OpenCode 会话和执行队列。',
      tone: 'warn',
    };
  }
  return null;
}

export function ExecutionNarrativePanel({
  task,
  stageLine,
  controlTone,
  currentDetail,
  nextActionText,
  approval,
  sched,
  stalledSec,
  dispatchDiagnosis,
  expectedAgent,
  traceId,
  outbox,
  activityData,
  evidenceData,
}: ExecutionNarrativePanelProps) {
  const actions = recentActions(activityData, task);
  const decisionSteps = buildDecisionSteps({ approval, dispatchDiagnosis, sched, stalledSec, nextActionText, expectedAgent, task });
  const failure = failureExplanation({ dispatchDiagnosis, sched, evidenceData, outbox });
  const statusTone = approval?.visible ? 'warn' : controlTone || dispatchDiagnosis?.tone || 'idle';
  const activeTodo = (task.todos || []).find((todo) => todo.status === 'in-progress');
  const meta = [
    expectedAgent ? `目标 ${expectedAgent}` : '',
    outbox ? `队列 ${outboxLabel(outbox)}` : '',
    traceId ? `trace ${shortTrace(traceId)}` : '',
    activityData?.lastActive ? `最后活跃 ${formatDashboardDateTime(activityData.lastActive)}` : '',
  ].filter(Boolean);

  return (
    <section className={`execution-narrative ${statusTone}`}>
      <div className="en-head">
        <div>
          <span className="en-kicker">执行叙事</span>
          <h3>{approval?.visible ? approval.title || '等待人工确认' : stageLine || stateLabel(task)}</h3>
          <p>{approval?.visible ? approval.reason || currentDetail : currentDetail}</p>
        </div>
        <span className={`en-state ${statusTone}`}>{toneLabel(statusTone)}</span>
      </div>

      <div className="en-current">
        <div className="en-current-main">
          <CircleDashed size={18} />
          <div>
            <span>现在</span>
            <b>{activeTodo ? activeTodo.title : nextActionText}</b>
          </div>
        </div>
        <div className="en-meta">
          {meta.length ? meta.map((item) => <span key={item}>{item}</span>) : <span>等待运行证据</span>}
        </div>
      </div>

      <div className="en-grid">
        <div className="en-card">
          <div className="en-card-head"><GitBranch size={15} /><span>判断路径</span></div>
          <div className="en-steps">
            {decisionSteps.map((step) => (
              <div className={`en-step ${step.tone}`} key={`${step.label}-${step.detail}`}>
                <b>{step.label}</b>
                <span>{step.detail}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="en-card">
          <div className="en-card-head"><Activity size={15} /><span>最近动作</span></div>
          <div className="en-actions">
            {actions.length ? actions.map((action, index) => (
              <div className={`en-action ${action.tone}`} key={`${action.title}-${index}`}>
                <span>{action.tone === 'ok' ? <CheckCircle2 size={13} /> : action.tone === 'err' ? <AlertTriangle size={13} /> : <ListChecks size={13} />}</span>
                <div>
                  <b>{action.title}</b>
                  <em>{action.detail}</em>
                </div>
                {action.meta && <small>{action.meta}</small>}
              </div>
            )) : (
              <div className="en-empty">还没有 Agent 动作记录；等待首次 progress / tool / flow 回写。</div>
            )}
          </div>
        </div>
      </div>

      {failure && (
        <div className={`en-failure ${failure.tone}`}>
          <AlertTriangle size={16} />
          <div>
            <b>{failure.title}</b>
            <span>{failure.detail}</span>
            <em>{failure.next}</em>
          </div>
        </div>
      )}
    </section>
  );
}
