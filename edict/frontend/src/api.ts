/**
 * API 层 — 对接 dashboard/server.py
 * 生产环境从同源 (port 7891) 请求，开发环境可通过 VITE_API_URL 指定
 */

const API_BASE = import.meta.env.VITE_API_URL || '';

// ── 通用请求 ──

async function fetchJ<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error(String(res.status));
  return res.json();
}

async function postJ<T>(url: string, data: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return res.json();
}

// ── API 接口 ──

export const api = {
  // 核心数据
  liveStatus: () => fetchJ<LiveStatus>(`${API_BASE}/api/live-status`),
  agentConfig: () => fetchJ<AgentConfig>(`${API_BASE}/api/agent-config`),
  modelChangeLog: () => fetchJ<ChangeLogEntry[]>(`${API_BASE}/api/model-change-log`).catch(() => []),
  officialsStats: () => fetchJ<OfficialsData>(`${API_BASE}/api/officials-stats`),
  morningBrief: () => fetchJ<MorningBrief>(`${API_BASE}/api/morning-brief`),
  morningConfig: () => fetchJ<SubConfig>(`${API_BASE}/api/morning-config`),
  agentsStatus: () => fetchJ<AgentsStatusData>(`${API_BASE}/api/agents-status`),
  runtimeOutbox: () => fetchJ<RuntimeOutboxHealth>(`${API_BASE}/api/runtime-outbox`),
  capabilities: () => fetchJ<CapabilitiesResult>(`${API_BASE}/api/capabilities`),
  runSpecs: (limit = 100) => fetchJ<RunSpecsResult>(`${API_BASE}/api/run-specs?limit=${limit}`),
  outputFiles: () => fetchJ<OutputFilesResult>(`${API_BASE}/api/output-files`),
  outputFileUrl: (path: string, download = false) =>
    `${API_BASE}/api/output-file?path=${encodeURIComponent(path)}${download ? '&download=1' : ''}`,
  sourceFile: (path: string, start = 0, end = 0, context = 4) =>
    fetchJ<SourceFileResult>(
      `${API_BASE}/api/source-file?path=${encodeURIComponent(path)}&start=${start}&end=${end}&context=${context}`
    ),
  openSourceFile: (path: string, startLine = 0) =>
    postJ<ActionResult & { path?: string; absolutePath?: string; line?: number; editor?: string }>(
      `${API_BASE}/api/open-source-file`,
      { path, startLine }
    ),

  // 任务实时动态
  taskActivity: (id: string) =>
    fetchJ<TaskActivityData>(`${API_BASE}/api/task-activity/${encodeURIComponent(id)}`),
  codingSession: (id: string) =>
    fetchJ<CodingSessionData>(`${API_BASE}/api/coding-session/${encodeURIComponent(id)}`),
  schedulerState: (id: string) =>
    fetchJ<SchedulerStateData>(`${API_BASE}/api/scheduler-state/${encodeURIComponent(id)}`),

  // 技能内容
  skillContent: (agentId: string, skillName: string) =>
    fetchJ<SkillContentResult>(
      `${API_BASE}/api/skill-content/${encodeURIComponent(agentId)}/${encodeURIComponent(skillName)}`
    ),

  // 操作类
  setModel: (agentId: string, model: string) =>
    postJ<ActionResult>(`${API_BASE}/api/set-model`, { agentId, model }),
  setDispatchChannel: (channel: string) =>
    postJ<ActionResult>(`${API_BASE}/api/set-dispatch-channel`, { channel }),
  agentWake: (agentId: string) =>
    postJ<ActionResult>(`${API_BASE}/api/agent-wake`, { agentId }),
  taskAction: (taskId: string, action: string, reason: string) =>
    postJ<ActionResult>(`${API_BASE}/api/task-action`, { taskId, action, reason }),
  reviewAction: (taskId: string, action: string, comment: string) =>
    postJ<ActionResult>(`${API_BASE}/api/review-action`, { taskId, action, comment }),
  advanceState: (taskId: string, comment: string) =>
    postJ<ActionResult>(`${API_BASE}/api/advance-state`, { taskId, comment }),
  archiveTask: (taskId: string, archived: boolean) =>
    postJ<ActionResult>(`${API_BASE}/api/archive-task`, { taskId, archived }),
  archiveAllDone: () =>
    postJ<ActionResult & { count?: number }>(`${API_BASE}/api/archive-task`, { archiveAllDone: true }),
  schedulerScan: (thresholdSec = 180) =>
    postJ<ActionResult & { count?: number; actions?: ScanAction[]; checkedAt?: string }>(
      `${API_BASE}/api/scheduler-scan`,
      { thresholdSec }
    ),
  schedulerRetry: (taskId: string, reason: string) =>
    postJ<ActionResult>(`${API_BASE}/api/scheduler-retry`, { taskId, reason }),
  schedulerEscalate: (taskId: string, reason: string) =>
    postJ<ActionResult>(`${API_BASE}/api/scheduler-escalate`, { taskId, reason }),
  schedulerRollback: (taskId: string, reason: string) =>
    postJ<ActionResult>(`${API_BASE}/api/scheduler-rollback`, { taskId, reason }),
  runtimeOutboxRetry: (itemId: string, reason = 'manual retry from dashboard') =>
    postJ<ActionResult & { item?: RuntimeOutboxItem }>(`${API_BASE}/api/runtime-outbox/retry`, { itemId, reason }),
  runtimeOutboxArchive: (params: { itemId?: string; taskId?: string; archiveAllFailed?: boolean; reason?: string }) =>
    postJ<ActionResult & { count?: number }>(`${API_BASE}/api/runtime-outbox/archive`, params),
  patchReviews: (taskId: string) =>
    fetchJ<{ ok: boolean; taskId: string; reviews: PatchReview[] }>(
      `${API_BASE}/api/patch-reviews/${encodeURIComponent(taskId)}`
    ),
  createPatchReview: (taskId: string, paths: string[]) =>
    postJ<ActionResult & { review?: PatchReview }>(`${API_BASE}/api/patch-review/create`, { taskId, paths }),
  patchReviewAction: (patchId: string, action: 'approve' | 'reject', reason = '') =>
    postJ<ActionResult & { review?: PatchReview }>(`${API_BASE}/api/patch-review/action`, { patchId, action, reason }),
  refreshMorning: () =>
    postJ<ActionResult>(`${API_BASE}/api/morning-brief/refresh`, {}),
  saveMorningConfig: (config: SubConfig) =>
    postJ<ActionResult>(`${API_BASE}/api/morning-config`, config),
  addSkill: (agentId: string, skillName: string, description: string, trigger: string) =>
    postJ<ActionResult>(`${API_BASE}/api/add-skill`, { agentId, skillName, description, trigger }),

  // 远程 Skills 管理
  addRemoteSkill: (agentId: string, skillName: string, sourceUrl: string, description?: string) =>
    postJ<ActionResult & { skillName?: string; agentId?: string; source?: string; localPath?: string; size?: number; addedAt?: string }>(
      `${API_BASE}/api/add-remote-skill`, { agentId, skillName, sourceUrl, description: description || '' }
    ),
  remoteSkillsList: () =>
    fetchJ<RemoteSkillsListResult>(`${API_BASE}/api/remote-skills-list`),
  updateRemoteSkill: (agentId: string, skillName: string) =>
    postJ<ActionResult>(`${API_BASE}/api/update-remote-skill`, { agentId, skillName }),
  removeRemoteSkill: (agentId: string, skillName: string) =>
    postJ<ActionResult>(`${API_BASE}/api/remove-remote-skill`, { agentId, skillName }),

  createTask: (data: CreateTaskPayload) =>
    postJ<ActionResult & { taskId?: string }>(`${API_BASE}/api/create-task`, data),
  previewRun: (data: RunCreatePayload) =>
    postJ<ActionResult & { run?: RunSpec }>(`${API_BASE}/api/runs/preview`, data),
  createRun: (data: RunCreatePayload) =>
    postJ<ActionResult & { taskId?: string; run?: RunSpec }>(`${API_BASE}/api/runs/create`, data),

  // ── 朝堂议政 ──
  courtDiscussStart: (topic: string, officials: string[], taskId?: string) =>
    postJ<CourtDiscussResult>(`${API_BASE}/api/court-discuss/start`, { topic, officials, taskId }),
  courtDiscussAdvance: (sessionId: string, userMessage?: string, decree?: string) =>
    postJ<CourtDiscussResult>(`${API_BASE}/api/court-discuss/advance`, { sessionId, userMessage, decree }),
  courtDiscussConclude: (sessionId: string) =>
    postJ<ActionResult & { summary?: string }>(`${API_BASE}/api/court-discuss/conclude`, { sessionId }),
  courtDiscussDestroy: (sessionId: string) =>
    postJ<ActionResult>(`${API_BASE}/api/court-discuss/destroy`, { sessionId }),
  courtDiscussFate: () =>
    fetchJ<{ ok: boolean; event: string }>(`${API_BASE}/api/court-discuss/fate`),
};

// ── Types ──

export interface ActionResult {
  ok: boolean;
  message?: string;
  error?: string;
}

export interface CapabilityCategory {
  id: string;
  label: string;
  description?: string;
}

export interface CapabilityInfo {
  id: string;
  name: string;
  category: string;
  categoryLabel?: string;
  description: string;
  adapters: string[];
  risk: 'low' | 'medium' | 'high' | string;
  enabled: boolean;
  tags: string[];
  inputs?: string[];
  outputs?: string[];
  permissions?: string[];
  permissionLabels?: string[];
  requiresApproval?: boolean;
  availability?: CapabilityAvailability;
}

export interface CapabilityAvailability {
  status?: 'ready' | 'configured' | 'missing' | 'unknown' | string;
  label?: string;
  reason?: string;
}

export interface CapabilityPolicy {
  id: string;
  name: string;
  category?: string;
  categoryLabel?: string;
  risk?: 'low' | 'medium' | 'high' | string;
  permissions?: string[];
  permissionLabels?: string[];
  requiresApproval?: boolean;
  availability?: CapabilityAvailability;
}

export interface ToolPolicy {
  permissions: string[];
  permissionLabels?: string[];
  requiresApproval: boolean;
  approvalReason?: string;
  unavailableCapabilities?: { id?: string; name?: string; reason?: string }[];
  unknownCapabilities?: { id?: string; name?: string; reason?: string }[];
}

export interface PolicyGate {
  decision: 'auto_dispatch' | 'hold_for_review' | 'hold_for_clarification' | 'hold_for_policy' | string;
  status: string;
  label: string;
  reason?: string;
  releaseAction?: string;
  riskLevel?: 'low' | 'medium' | 'high' | string;
  requiresApproval?: boolean;
  permissions?: string[];
  permissionLabels?: string[];
}

export interface ExecutionIsolation {
  mode: string;
  targetMode?: string;
  status?: 'required' | 'recommended' | 'optional' | 'not_required' | string;
  label?: string;
  required?: boolean;
  patchFirst?: boolean;
  requiresPatchReview?: boolean;
  checkpoint?: string;
  rollback?: string;
  reason?: string;
  guardrails?: string[];
  previousMode?: string;
  worktreePath?: string;
  worktreeBranch?: string;
  baseHead?: string;
  allocatedAt?: string;
  lastError?: string;
}

export interface CapabilitiesResult {
  ok: boolean;
  generatedAt: string;
  categories: CapabilityCategory[];
  capabilities: CapabilityInfo[];
}

export interface GovernanceStage {
  stage: string;
  dept: string;
  label: string;
}

export interface RunSpec {
  id: string;
  taskId: string;
  title: string;
  goal: string;
  mode: 'auto' | 'plan' | 'execute' | 'interactive' | string;
  requestedMode?: 'auto' | 'plan' | 'execute' | 'interactive' | string;
  requestedPriority?: 'auto' | 'low' | 'normal' | 'high' | string;
  intent?: {
    requestedMode?: string;
    mode?: string;
    reason?: string;
    clarification?: RunClarification;
  };
  clarification?: RunClarification;
  status: string;
  runKind: string;
  targetDept: string;
  priority: string;
  requiredCapabilities: string[];
  capabilityPolicies?: CapabilityPolicy[];
  toolPolicy?: ToolPolicy;
  policyGate?: PolicyGate;
  executionIsolation?: ExecutionIsolation;
  riskLevel: 'low' | 'medium' | 'high' | string;
  governance: GovernanceStage[];
  constraints?: string;
  deliverable?: string;
  profile?: RunProfile;
  createdAt: string;
  updatedAt: string;
}

export interface RunProfileField {
  value: string;
  source?: 'user' | 'inferred' | string;
  requested?: string;
}

export interface RunProfile {
  deliverable?: RunProfileField;
  constraints?: RunProfileField;
  priority?: RunProfileField;
  targetDept?: RunProfileField;
  clarification?: RunClarification;
}

export interface RunClarification {
  level: 'clear' | 'needs_detail' | 'ambiguous' | string;
  score: number;
  shouldAsk: boolean;
  missing: string[];
  questions: string[];
  quickAdds?: Array<{ label: string; append: string }>;
  safetyMode?: string;
  primaryQuestion?: string;
  summary?: string;
}

export interface RunSpecsResult {
  ok: boolean;
  count: number;
  runs: RunSpec[];
}

export interface RunCreatePayload {
  goal: string;
  mode?: 'auto' | 'plan' | 'execute' | 'interactive';
  priority?: string;
  targetDept?: string;
  capabilityIds?: string[];
  constraints?: string;
  deliverable?: string;
}

export interface OutputFile {
  path: string;
  absolutePath: string;
  name: string;
  ext: string;
  kind: string;
  source: string;
  size: number;
  sizeLabel: string;
  mtime: string;
  viewUrl: string;
  downloadUrl: string;
  taskId?: string;
  taskTitle?: string;
}

export interface OutputFilesResult {
  ok: boolean;
  root: string;
  generatedAt: string;
  count: number;
  files: OutputFile[];
  groups?: OutputGroup[];
}

export interface OutputGroup {
  taskId: string;
  taskTitle: string;
  state: string;
  org: string;
  updatedAt: string;
  outputText: string;
  files: OutputFile[];
}

export interface SourceLine {
  no: number;
  text: string;
  highlight: boolean;
}

export interface SourceFileResult {
  ok: boolean;
  error?: string;
  path: string;
  absolutePath: string;
  startLine: number;
  endLine: number;
  viewStart: number;
  viewEnd: number;
  totalLines: number;
  lines: SourceLine[];
}

export interface FlowEntry {
  at: string;
  from: string;
  to: string;
  remark: string;
}

export interface TodoItem {
  id: string | number;
  title: string;
  status: 'not-started' | 'in-progress' | 'completed';
  detail?: string;
}

export interface Heartbeat {
  status: 'active' | 'warn' | 'stalled' | 'unknown' | 'idle';
  label: string;
}

export interface Task {
  id: string;
  title: string;
  state: string;
  org: string;
  now: string;
  eta: string;
  block: string;
  ac: string;
  output: string;
  heartbeat: Heartbeat;
  flow_log: FlowEntry[];
  todos: TodoItem[];
  review_round: number;
  archived: boolean;
  archivedAt?: string;
  updatedAt?: string;
  traceId?: string;
  trace_id?: string;
  sourceMeta?: Record<string, unknown>;
  activity?: ActivityEntry[];
  _prev_state?: string;
  _scheduler?: SchedulerInfo;
}

export interface SyncStatus {
  ok: boolean;
  [key: string]: unknown;
}

export interface LiveStatus {
  tasks: Task[];
  syncStatus: SyncStatus;
}

export interface AgentInfo {
  id: string;
  label: string;
  emoji: string;
  role: string;
  model: string;
  defaultModel?: string;
  skills: SkillInfo[];
}

export interface SkillInfo {
  name: string;
  description: string;
  path: string;
}

export interface KnownModel {
  id: string;
  label: string;
  provider: string;
}

export interface AgentConfig {
  agents: AgentInfo[];
  defaultModel?: string;
  knownModels?: KnownModel[];
  dispatchChannel?: string;
  runtime?: string;
}

export interface ChangeLogEntry {
  at: string;
  agentId: string;
  oldModel: string;
  newModel: string;
  rolledBack?: boolean;
}

export interface OfficialInfo {
  id: string;
  label: string;
  emoji: string;
  role: string;
  rank: string;
  model: string;
  model_short: string;
  tokens_in: number;
  tokens_out: number;
  cache_read: number;
  cache_write: number;
  cost_cny: number;
  cost_usd: number;
  sessions: number;
  messages: number;
  tasks_done: number;
  tasks_active: number;
  flow_participations: number;
  merit_score: number;
  merit_rank: number;
  last_active: string;
  heartbeat: Heartbeat;
  participated_edicts: { id: string; title: string; state: string }[];
}

export interface OfficialsData {
  officials: OfficialInfo[];
  totals: { tasks_done: number; cost_cny: number };
  top_official: string;
}

export interface AgentStatusInfo {
  id: string;
  label: string;
  emoji: string;
  role: string;
  status: 'running' | 'idle' | 'offline' | 'unconfigured';
  statusLabel: string;
  lastActive?: string;
}

export interface GatewayStatus {
  alive: boolean;
  probe: boolean;
  runtime?: 'openclaw' | 'opencode' | string;
  label?: string;
  status: string;
}

export interface AgentsStatusData {
  ok: boolean;
  gateway: GatewayStatus;
  agents: AgentStatusInfo[];
  checkedAt: string;
}

export interface RuntimeOutboxItem {
  id: string;
  kind: string;
  status: string;
  taskId: string;
  taskTitle?: string;
  taskState?: string;
  state?: string;
  agentId?: string;
  trigger?: string;
  traceId?: string;
  attempts?: number;
  maxAttempts?: number;
  createdAt?: string;
  updatedAt?: string;
  claimedAt?: string;
  finishedAt?: string;
  lastError?: string;
  ageSec?: number;
  ageText?: string;
  result?: Record<string, unknown>;
}

export interface RuntimeOutboxHealth {
  ok: boolean;
  checkedAt: string;
  worker: {
    active: boolean;
    workerId: string;
    startedAt?: string;
    heartbeatAt?: string;
    heartbeatAgeSec?: number | null;
    heartbeatAgeText?: string;
    stoppedAt?: string;
  };
  counts: Record<string, number>;
  total: number;
  pending: number;
  running: number;
  failed: number;
  archived?: number;
  done: number;
  oldestPendingAgeSec: number;
  oldestPendingAgeText: string;
  oldestRunningAgeSec?: number;
  oldestRunningAgeText?: string;
  trend?: {
    windowSec?: number;
    windowText?: string;
    enqueued?: number;
    completed?: number;
    failed?: number;
    label?: string;
  };
  summary?: {
    tone?: 'ok' | 'warn' | 'err' | 'idle' | string;
    label?: string;
    detail?: string;
    nextAction?: string;
  };
  latest?: RuntimeOutboxItem;
  activeItems: RuntimeOutboxItem[];
  deadLetters: RuntimeOutboxItem[];
  deadLetterWindow?: { total?: number; returned?: number; truncated?: boolean };
}

export interface MorningNewsItem {
  title: string;
  summary?: string;
  desc?: string;
  link: string;
  source: string;
  image?: string;
  pub_date?: string;
}

export interface MorningBrief {
  date?: string;
  generated_at?: string;
  categories: Record<string, MorningNewsItem[]>;
}

export interface SubCategoryConfig {
  name: string;
  enabled: boolean;
}

export interface CustomFeed {
  name: string;
  url: string;
  category: string;
}

export interface SubConfig {
  categories: SubCategoryConfig[];
  keywords: string[];
  custom_feeds: CustomFeed[];
  feishu_webhook: string;
}

export interface ActivityEntry {
  kind: string;
  at?: number | string;
  text?: string;
  thinking?: string;
  agent?: string;
  eventId?: string;
  traceId?: string;
  eventKind?: string;
  source?: string;
  confidence?: string;
  from?: string;
  to?: string;
  remark?: string;
  tools?: { name: string; input_preview?: string; input?: Record<string, unknown> }[];
  tool?: string;
  output?: string;
  exitCode?: number | null;
  items?: TodoItem[];
  diff?: {
    changed?: { id: string; from: string; to: string }[];
    added?: { id: string; title: string }[];
    removed?: { id: string; title: string }[];
  };
}

export interface CodingEvent {
  kind: string;
  title: string;
  at: string;
  agent: string;
  detail: string;
  path: string;
  command: string;
  status: string;
  source: string;
  startLine: number;
  endLine: number;
  sourceUrl: string;
  meta?: Record<string, unknown>;
}

export interface CodingFileRef {
  path: string;
  reads: number;
  changes: number;
  outputs: number;
  latestAt: string;
  lastStartLine?: number;
  lastEndLine?: number;
  sourceUrl?: string;
}

export interface CodingSessionSummary {
  todoTotal: number;
  todoDone: number;
  fileCount: number;
  commandCount: number;
  testCount: number;
  outputCount: number;
  eventCount: number;
  hasPatchReview: boolean;
  pendingPatchCount?: number;
  approvedPatchCount?: number;
  rejectedPatchCount?: number;
  hasSourcePreview: boolean;
  hasWorktreeCheckpoint: boolean;
  confidence?: string;
}

export interface WorktreeFile {
  path: string;
  status: string;
  kind: string;
}

export interface WorktreeCheckpoint {
  ok: boolean;
  available?: boolean;
  error?: string;
  root?: string;
  branch?: string;
  head?: string;
  dirty?: boolean;
  stagedCount?: number;
  unstagedCount?: number;
  untrackedCount?: number;
  fileCount?: number;
  files?: WorktreeFile[];
  generatedAt?: string;
}

export interface PatchReviewFileStat {
  path: string;
  insertions: number;
  deletions: number;
  status?: string;
}

export interface PatchReview {
  id: string;
  taskId: string;
  status: 'pending' | 'approved' | 'rejected' | string;
  title: string;
  paths: string[];
  stats: {
    files?: PatchReviewFileStat[];
    insertions?: number;
    deletions?: number;
  };
  createdAt: string;
  updatedAt: string;
  decidedAt?: string;
  decidedBy?: string;
  decisionReason?: string;
  lastError?: string;
  baseHead?: string;
  worktreePath?: string;
  worktreeBranch?: string;
  projectRoot?: string;
  diffPreview: string;
  diffSize: number;
}

export interface CodingSessionData {
  ok: boolean;
  error?: string;
  taskId: string;
  sessionId: string;
  runtime: string;
  task: {
    title: string;
    state: string;
    org: string;
    updatedAt: string;
  };
  summary: CodingSessionSummary;
  files: CodingFileRef[];
  commands: CodingEvent[];
  tests: CodingEvent[];
  outputs: CodingEvent[];
  events: CodingEvent[];
  executionIsolation?: ExecutionIsolation;
  patchReviews?: PatchReview[];
  checkpoint?: WorktreeCheckpoint;
  missingLayers: string[];
}

export interface PhaseDuration {
  phase: string;
  durationSec: number;
  durationText: string;
  ongoing?: boolean;
}

export interface TodosSummary {
  total: number;
  completed: number;
  inProgress: number;
  notStarted: number;
  percent: number;
}

export interface ResourceSummary {
  totalTokens?: number;
  totalCost?: number;
  totalElapsedSec?: number;
}

export interface StateEvidence {
  confidence: string;
  label: string;
  eventCount: number;
  latestEventKind?: string;
  latestEventAt?: string;
  lastObservedAt?: string;
  ageSec?: number | null;
  sources?: string[];
}

export interface OutboxSummary {
  total: number;
  pending: number;
  running: number;
  failed: number;
  counts?: Record<string, number>;
  latest?: Record<string, unknown>;
}

export interface TraceSummary {
  traceId: string;
  eventKinds?: Record<string, number>;
  sources?: Record<string, number>;
  outbox?: OutboxSummary;
  latestAt?: string;
}

export interface TaskActivityData {
  ok: boolean;
  traceId?: string;
  message?: string;
  error?: string;
  activity?: ActivityEntry[];
  activityWindow?: { total?: number; returned?: number; truncated?: boolean };
  relatedAgents?: string[];
  outputGroup?: OutputGroup | null;
  agentLabel?: string;
  lastActive?: string;
  phaseDurations?: PhaseDuration[];
  totalDuration?: string;
  todosSummary?: TodosSummary;
  resourceSummary?: ResourceSummary;
  stateEvidence?: StateEvidence;
  traceSummary?: TraceSummary;
}

export interface SchedulerInfo {
  retryCount?: number;
  escalationLevel?: number;
  lastDispatchStatus?: string;
  lastDispatchState?: string;
  stallThresholdSec?: number;
  enabled?: boolean;
  lastProgressAt?: string;
  lastDispatchAt?: string;
  lastDispatchAgent?: string;
  lastDispatchError?: string;
  lastDispatchSession?: string;
  lastDispatchTraceId?: string;
  lastDispatchRuntime?: string;
  lastDispatchSessionBoundAt?: string;
  autoRollback?: boolean;
}

export interface RuntimeSessionBinding {
  status?: 'bound' | 'unbound' | 'trace-mismatch' | string;
  bound?: boolean;
  sessionId?: string;
  traceId?: string;
  agentId?: string;
  runtime?: string;
  dispatchId?: string;
  trigger?: string;
  state?: string;
  boundAt?: string;
}

export interface SchedulerStateData {
  ok: boolean;
  error?: string;
  traceId?: string;
  expectedAgent?: string;
  outbox?: OutboxSummary;
  scheduler?: SchedulerInfo;
  runtimeSession?: RuntimeSessionBinding;
  stalledSec?: number;
  dispatchDiagnosis?: {
    tone?: 'ok' | 'warn' | 'err' | 'idle' | string;
    label?: string;
    detail?: string;
    nextAction?: string;
    action?: 'scan' | 'retry' | 'escalate' | 'rollback' | 'none' | string;
    actionLabel?: string;
    actionReason?: string;
    retryable?: boolean;
  };
}

export interface SkillContentResult {
  ok: boolean;
  name?: string;
  agent?: string;
  content?: string;
  path?: string;
  error?: string;
}

export interface ScanAction {
  taskId: string;
  action: string;
  to?: string;
  toState?: string;
  stalledSec?: number;
}

export interface CreateTaskPayload {
  title: string;
  org: string;
  targetDept?: string;
  priority?: string;
  templateId?: string;
  params?: Record<string, string>;
}

export interface RemoteSkillItem {
  skillName: string;
  agentId: string;
  sourceUrl: string;
  description: string;
  localPath: string;
  addedAt: string;
  lastUpdated: string;
  status: 'valid' | 'not-found' | string;
}

export interface RemoteSkillsListResult {
  ok: boolean;
  remoteSkills?: RemoteSkillItem[];
  count?: number;
  listedAt?: string;
  error?: string;
}

// ── 朝堂议政 ──

export interface CourtDiscussResult {
  ok: boolean;
  session_id?: string;
  topic?: string;
  round?: number;
  new_messages?: Array<{
    official_id: string;
    name: string;
    content: string;
    emotion?: string;
    action?: string;
  }>;
  scene_note?: string;
  total_messages?: number;
  error?: string;
}
