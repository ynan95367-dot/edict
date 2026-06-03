import { useEffect, useMemo, useState } from 'react';
import {
  Archive,
  Bot,
  CheckCircle2,
  Code2,
  FileText,
  FolderOpen,
  Globe,
  Layers3,
  Play,
  Route,
  Send,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  TerminalSquare,
  Workflow,
  type LucideIcon,
} from 'lucide-react';
import { api, type CapabilityInfo, type CapabilityPolicy, type RunSpec } from '../api';
import { useStore } from '../store';

type RunMode = 'auto' | 'plan' | 'execute' | 'interactive';
type PriorityMode = 'auto' | 'normal' | 'high' | 'low';

const MODE_OPTIONS: { id: RunMode; label: string; detail: string }[] = [
  { id: 'auto', label: '自动识别', detail: '按目标判断怎么做' },
  { id: 'execute', label: '执行', detail: '形成方案后进入分发' },
  { id: 'plan', label: '优先方案', detail: '不预设直接改动' },
  { id: 'interactive', label: '边做边问', detail: '关键节点等待确认' },
];

const PRIORITIES: { id: PriorityMode; label: string }[] = [
  { id: 'auto', label: '自动' },
  { id: 'normal', label: '普通' },
  { id: 'high', label: '加急' },
  { id: 'low', label: '低优先' },
];

const CATEGORY_ICONS: Record<string, LucideIcon> = {
  runtime: Bot,
  code: Code2,
  file: FolderOpen,
  shell: TerminalSquare,
  browser: Globe,
  document: FileText,
  artifact: Archive,
  governance: ShieldCheck,
};

const CATEGORY_ORDER = ['runtime', 'code', 'file', 'shell', 'browser', 'document', 'artifact', 'governance'];

const riskLabel: Record<string, string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
};

const priorityLabel: Record<string, string> = {
  low: '低优先',
  normal: '普通',
  high: '加急',
};

const statusLabel: Record<string, string> = {
  preview: '预览中',
  created: '可分发',
  waiting_review: '等待审议',
  waiting_clarification: '等待补充',
  waiting_policy_approval: '等待权限审批',
};

const availabilityFallback: Record<string, string> = {
  ready: '可用',
  configured: '已配置',
  missing: '待配置',
  unknown: '按任务连接',
};

const permissionFallback: Record<string, string> = {
  'agent.run': '调用 Agent',
  'workspace.read': '读工作区',
  'workspace.write': '写工作区',
  'shell.execute': '执行命令',
  'browser.control': '控制浏览器',
  'network.local': '本地服务',
  'network.web': '访问网络',
  'document.read': '读文档',
  'document.write': '写文档',
  'artifact.write': '沉淀产物',
  'policy.review': '治理审议',
};

function availabilityClass(status?: string) {
  if (status === 'ready' || status === 'configured' || status === 'missing') return status;
  return 'unknown';
}

function permissionLabels(item?: Pick<CapabilityInfo, 'permissions' | 'permissionLabels'> | Pick<CapabilityPolicy, 'permissions' | 'permissionLabels'>) {
  if (!item) return [];
  if (item.permissionLabels?.length) return item.permissionLabels;
  return (item.permissions || []).map((perm) => permissionFallback[perm] || perm);
}

export default function CommandCenter() {
  const toast = useStore((s) => s.toast);
  const loadAll = useStore((s) => s.loadAll);
  const setActiveTab = useStore((s) => s.setActiveTab);
  const setModalTaskId = useStore((s) => s.setModalTaskId);

  const [goal, setGoal] = useState('');
  const [mode, setMode] = useState<RunMode>('auto');
  const [showRunControls, setShowRunControls] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [priority, setPriority] = useState<PriorityMode>('auto');
  const [deliverable, setDeliverable] = useState('');
  const [constraints, setConstraints] = useState('');
  const [capabilities, setCapabilities] = useState<CapabilityInfo[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [manualCaps, setManualCaps] = useState(false);
  const [loadingCaps, setLoadingCaps] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [lastRun, setLastRun] = useState<RunSpec | null>(null);
  const [previewRun, setPreviewRun] = useState<RunSpec | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');

  useEffect(() => {
    let alive = true;
    api.capabilities()
      .then((res) => {
        if (!alive) return;
        setCapabilities(res.capabilities || []);
      })
      .catch(() => toast('能力注册表读取失败', 'err'))
      .finally(() => {
        if (alive) setLoadingCaps(false);
      });
    return () => {
      alive = false;
    };
  }, [toast]);

  useEffect(() => {
    const text = goal.trim();
    if (!text) {
      setPreviewRun(null);
      setPreviewError('');
      setPreviewLoading(false);
      return;
    }

    let alive = true;
    setPreviewLoading(true);
    const timer = window.setTimeout(() => {
      api.previewRun({
        goal: text,
        mode,
        priority,
        deliverable: deliverable.trim(),
        constraints: constraints.trim(),
        capabilityIds: manualCaps ? selectedIds : [],
      })
        .then((result) => {
          if (!alive) return;
          if (result.ok && result.run) {
            setPreviewRun(result.run);
            setPreviewError('');
          } else {
            setPreviewRun(null);
            setPreviewError(result.error || 'RunSpec 预览失败');
          }
        })
        .catch(() => {
          if (!alive) return;
          setPreviewRun(null);
          setPreviewError('RunSpec 预览连接失败');
        })
        .finally(() => {
          if (alive) setPreviewLoading(false);
        });
    }, 220);

    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [goal, mode, priority, deliverable, constraints, manualCaps, selectedIds]);

  const effectiveIds = previewRun?.requiredCapabilities || (manualCaps ? selectedIds : []);
  const selectedCaps = useMemo(
    () => capabilities.filter((cap) => effectiveIds.includes(cap.id)),
    [capabilities, effectiveIds]
  );
  const groupedCaps = useMemo(() => {
    const groups = new Map<string, CapabilityInfo[]>();
    for (const cap of capabilities.filter((item) => item.enabled)) {
      if (!groups.has(cap.category)) groups.set(cap.category, []);
      groups.get(cap.category)!.push(cap);
    }
    return Array.from(groups.entries()).sort(([a], [b]) => CATEGORY_ORDER.indexOf(a) - CATEGORY_ORDER.indexOf(b));
  }, [capabilities]);

  const risk = previewRun?.riskLevel || 'low';
  const effectiveMode = (previewRun?.mode as RunMode | undefined) || (mode === 'auto' ? 'execute' : mode);
  const selectedMode = MODE_OPTIONS.find((item) => item.id === effectiveMode);
  const governance = previewRun?.governance || [];
  const targetDept = previewRun?.targetDept || '尚书省';
  const effectivePriority = (previewRun?.priority as PriorityMode | undefined) || (priority === 'auto' ? 'normal' : priority);
  const previewDeliverable = previewRun?.deliverable || '等待目标后自动生成';
  const previewConstraints = previewRun?.constraints || '等待目标后自动生成';
  const previewTitle = previewRun?.title || goal.trim().split(/\n+/)[0] || '等待输入目标';
  const previewStatus = previewRun?.status || 'preview';
  const capabilityPolicies = previewRun?.capabilityPolicies || selectedCaps;
  const hasToolPolicy = !!previewRun?.toolPolicy;
  const policyGate = previewRun?.policyGate;
  const gateDecision = policyGate?.decision || '';
  const gateHeld = !!gateDecision && gateDecision !== 'auto_dispatch';
  const gateNeedsReview = gateHeld || !!previewRun?.toolPolicy?.requiresApproval;
  const gateLabel = !hasToolPolicy ? '等待目标' : policyGate?.label || (gateNeedsReview ? '需确认' : '可自动分发');
  const gateReason = policyGate?.reason || previewRun?.toolPolicy?.approvalReason || '等待目标后自动生成权限摘要';
  const toolPermissions = previewRun?.toolPolicy?.permissionLabels?.length
    ? previewRun.toolPolicy.permissionLabels
    : Array.from(new Set(capabilityPolicies.flatMap((item) => permissionLabels(item))));
  const unavailableCaps = previewRun?.toolPolicy?.unavailableCapabilities || [];
  const unknownCaps = previewRun?.toolPolicy?.unknownCapabilities || [];
  const executionIsolation = previewRun?.executionIsolation;
  const isolationTags = executionIsolation ? [
    executionIsolation.patchFirst ? 'Patch-first' : '按需 Patch',
    executionIsolation.requiresPatchReview ? '需要 Patch 审批' : '审批按需',
    executionIsolation.checkpoint ? `Checkpoint ${executionIsolation.checkpoint}` : '',
    executionIsolation.rollback ? `Rollback ${executionIsolation.rollback}` : '',
  ].filter(Boolean) : [];
  const clarification = previewRun?.clarification || previewRun?.profile?.clarification;
  const intentReason = previewLoading
    ? '正在识别目标...'
    : previewError || clarification?.summary || previewRun?.intent?.reason || (goal.trim() ? '等待后端识别' : '输入目标后自动识别');
  const priorityReason = priority === 'auto'
    ? previewRun?.profile?.priority?.source === 'inferred'
      ? '后端根据目标推断'
      : goal.trim()
        ? '等待后端判断'
        : '输入目标后自动判断'
    : '用户手动指定';

  const toggleCapability = (id: string) => {
    setManualCaps(true);
    setSelectedIds((current) => (
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id]
    ));
  };

  const resetAutoCaps = () => {
    setManualCaps(false);
    setSelectedIds([]);
  };

  const appendClarification = (text: string) => {
    if (!text) return;
    setGoal((current) => {
      const base = current.trim();
      if (!base) return text;
      if (base.includes(text)) return base;
      return `${base}，${text}`;
    });
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!goal.trim()) {
      toast('请先写清楚要完成的任务', 'err');
      return;
    }
    setSubmitting(true);
    try {
      const result = await api.createRun({
        goal: goal.trim(),
        mode,
        priority,
        deliverable: deliverable.trim(),
        constraints: constraints.trim(),
        capabilityIds: manualCaps ? selectedIds : [],
      });
      if (result.ok && result.run) {
        setLastRun(result.run);
        const resolvedMode = result.run.mode || effectiveMode;
        const resultGate = result.run.policyGate?.decision || '';
        toast(
          resultGate === 'hold_for_policy'
            ? `${result.taskId} 已生成 RunSpec，等待权限审批`
            : resolvedMode === 'plan'
            ? `${result.taskId} 已生成 RunSpec，等待审议`
            : resolvedMode === 'interactive'
              ? `${result.taskId} 已生成 RunSpec，等待补充确认`
            : `${result.taskId} 已进入太子分拣`,
          'ok'
        );
        await loadAll();
      } else {
        toast(result.error || '创建运行失败', 'err');
      }
    } catch {
      toast('服务器连接失败', 'err');
    } finally {
      setSubmitting(false);
    }
  };

  const openTask = () => {
    if (!lastRun?.taskId) return;
    setActiveTab('edicts');
    setModalTaskId(lastRun.taskId);
  };

  return (
    <div className="cmd-page">
      <form className="cmd-compose" onSubmit={submit}>
        <div className="cmd-section-head">
          <div>
            <div className="cmd-kicker">Agent Control Plane</div>
            <h2>下达指令</h2>
          </div>
          <div className={`cmd-risk ${risk}`}>{riskLabel[risk] || risk}</div>
        </div>

        <label className="cmd-field">
          <span>目标</span>
          <textarea
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            placeholder="例如：检查当前平台为什么 OpenCode 模型没有接入，并修复配置页面"
            rows={7}
          />
        </label>

        <div className="cmd-intent-panel">
          <div className="cmd-intent-main">
            <div className="cmd-intent-icon">
              <Sparkles size={16} />
            </div>
            <div>
              <span>意图识别</span>
              <b>
                {mode === 'auto'
                  ? `${selectedMode?.label || effectiveMode}（自动）`
                  : `${selectedMode?.label || effectiveMode}（手动）`}
              </b>
              <small>{mode === 'auto' ? intentReason : selectedMode?.detail}</small>
            </div>
          </div>
          <button type="button" className="cmd-ghost" onClick={() => setShowRunControls((value) => !value)}>
            {showRunControls ? '收起调整' : '手动调整'}
          </button>
        </div>

        {showRunControls && (
          <div className="cmd-mode-row" role="group" aria-label="运行方式">
            {MODE_OPTIONS.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`cmd-mode ${mode === item.id ? 'active' : ''}`}
                onClick={() => setMode(item.id)}
              >
                <span>{item.label}</span>
                <small>{item.detail}</small>
              </button>
            ))}
          </div>
        )}

        {clarification?.shouldAsk && (
          <div className={`cmd-clarify-panel ${clarification.level}`}>
            <div>
              <span>最小补充</span>
              <b>{clarification.primaryQuestion || '补一句目标对象或期望交付物就够。'}</b>
              {!!clarification.missing?.length && (
                <div className="cmd-clarify-tags">
                  {clarification.missing.map((item) => (
                    <em key={item}>{item}</em>
                  ))}
                </div>
              )}
              {!!clarification.quickAdds?.length && (
                <div className="cmd-clarify-actions">
                  {clarification.quickAdds.map((item) => (
                    <button key={item.append} type="button" onClick={() => appendClarification(item.append)}>
                      {item.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <small>{clarification.score}%</small>
          </div>
        )}

        <div className="cmd-profile-panel">
          <div className="cmd-profile-head">
            <div>
              <div className="cmd-block-title">自动任务画像</div>
              <div className="cmd-block-sub">交付物、执行边界和优先级会随目标自动更新</div>
            </div>
            <button type="button" className="cmd-ghost" onClick={() => setShowAdvanced((value) => !value)}>
              {showAdvanced ? '收起补充' : '补充设置'}
            </button>
          </div>
          <div className="cmd-profile-grid">
            <div>
              <span>交付物</span>
              <b>{previewDeliverable}</b>
            </div>
            <div>
              <span>执行边界</span>
              <b>{previewConstraints}</b>
            </div>
            <div>
              <span>优先级</span>
              <b>{priority === 'auto' ? `${priorityLabel[effectivePriority] || effectivePriority} · 自动` : priorityLabel[effectivePriority]}</b>
              <small>{priorityReason}</small>
            </div>
          </div>
        </div>

        {showAdvanced && (
          <div className="cmd-advanced-panel">
            <div className="cmd-inline-grid">
              <label className="cmd-field compact">
                <span>交付物</span>
                <input
                  value={deliverable}
                  onChange={(event) => setDeliverable(event.target.value)}
                  placeholder="报告、补丁、PPT、网页、表格..."
                />
              </label>
              <label className="cmd-field compact">
                <span>约束</span>
                <input
                  value={constraints}
                  onChange={(event) => setConstraints(event.target.value)}
                  placeholder="不联网、先给计划、只读、需审批..."
                />
              </label>
            </div>

            <div className="cmd-priority-row" role="group" aria-label="优先级">
              <SlidersHorizontal size={16} />
              {PRIORITIES.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`cmd-priority ${priority === item.id ? 'active' : ''}`}
                  onClick={() => setPriority(item.id)}
                >
                  {item.label}
                </button>
              ))}
            </div>

            <div className="cmd-cap-head">
              <div>
                <div className="cmd-block-title">能力</div>
                <div className="cmd-block-sub">{manualCaps ? '手动选择' : '按目标自动推断'}</div>
              </div>
              {manualCaps && (
                <button type="button" className="cmd-ghost" onClick={resetAutoCaps}>
                  恢复自动
                </button>
              )}
            </div>

            <div className="cmd-cap-grid">
              {loadingCaps ? (
                <div className="cmd-loading">读取能力注册表…</div>
              ) : (
                groupedCaps.map(([category, items]) => {
                  const Icon = CATEGORY_ICONS[category] || Layers3;
                  return (
                    <div className="cmd-cap-group" key={category}>
                      <div className="cmd-cap-group-title">
                        <Icon size={15} />
                        {items[0]?.categoryLabel || category}
                      </div>
                      <div className="cmd-cap-list">
                        {items.map((cap) => {
                          const status = availabilityClass(cap.availability?.status);
                          const labels = permissionLabels(cap).slice(0, 2);
                          return (
                            <button
                              type="button"
                              key={cap.id}
                              className={`cmd-cap ${effectiveIds.includes(cap.id) ? 'active' : ''} ${status}`}
                              onClick={() => toggleCapability(cap.id)}
                              title={cap.availability?.reason || cap.description}
                            >
                              <div className="cmd-cap-main">
                                <span>{cap.name}</span>
                                <small>{riskLabel[cap.risk] || cap.risk}</small>
                              </div>
                              <div className="cmd-cap-meta">
                                <b>{cap.availability?.label || availabilityFallback[status]}</b>
                                {!!labels.length && <i>{labels.join(' / ')}</i>}
                              </div>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}

        <div className="cmd-cap-head cmd-cap-summary">
          <div>
            <div className="cmd-block-title">能力</div>
            <div className="cmd-block-sub">
              {manualCaps ? `已手动选择 ${selectedCaps.length} 项` : `自动推断 ${selectedCaps.length} 项`}
            </div>
          </div>
          <button type="button" className="cmd-ghost" onClick={() => setShowAdvanced((value) => !value)}>
            调整能力
          </button>
        </div>

        <button className="cmd-submit" type="submit" disabled={submitting || !goal.trim()}>
          {submitting ? <Workflow size={17} /> : <Send size={17} />}
          {submitting
            ? '创建中'
            : gateDecision === 'hold_for_policy'
              ? '下达并等待审批'
              : effectiveMode === 'plan'
                ? '下达方案任务'
                : effectiveMode === 'interactive'
                  ? '下达并等待确认'
                  : '下达并分发'}
        </button>
      </form>

      <aside className="cmd-preview">
        <div className="cmd-preview-top">
          <div>
            <div className="cmd-kicker">RunSpec Preview</div>
            <h3>{previewTitle}</h3>
          </div>
          <Route size={22} />
        </div>

        <div className="cmd-spec-grid">
          <div>
            <span>运行方式</span>
            <b>{mode === 'auto' ? `${selectedMode?.label || effectiveMode} · 自动` : selectedMode?.label}</b>
          </div>
          <div>
            <span>目标部门</span>
            <b>{targetDept}</b>
          </div>
          <div>
            <span>能力数</span>
            <b>{selectedCaps.length}</b>
          </div>
          <div>
            <span>状态</span>
            <b>{statusLabel[previewStatus] || previewStatus}</b>
          </div>
          <div>
            <span>风险</span>
            <b>{riskLabel[risk] || risk}</b>
          </div>
        </div>

        <div className="cmd-preview-block">
          <div className="cmd-block-title">能力调用</div>
          <div className="cmd-selected-caps">
            {selectedCaps.length ? selectedCaps.map((cap) => (
              <span key={cap.id}>{cap.name}</span>
            )) : <em>等待目标</em>}
          </div>
        </div>

        <div className="cmd-preview-block">
          <div className="cmd-block-title">工具权限</div>
          <div className="cmd-policy-head">
            <span className={`cmd-policy-state ${hasToolPolicy && gateNeedsReview ? 'review' : 'auto'}`}>
              {gateLabel}
            </span>
            <small>{gateReason}</small>
          </div>
          <div className="cmd-policy-tags">
            {toolPermissions.length ? toolPermissions.slice(0, 8).map((item) => (
              <span key={item}>{item}</span>
            )) : <em>等待目标</em>}
          </div>
          {!!unavailableCaps.length && (
            <div className="cmd-policy-note">
              待配置：{unavailableCaps.map((item) => item.name || item.id).join('、')}
            </div>
          )}
          {!!unknownCaps.length && !unavailableCaps.length && (
            <div className="cmd-policy-note muted">
              任务中确认：{unknownCaps.map((item) => item.name || item.id).join('、')}
            </div>
          )}
        </div>

        <div className="cmd-preview-block">
          <div className="cmd-block-title">执行隔离</div>
          <div className="cmd-policy-head">
            <span className={`cmd-policy-state ${executionIsolation?.required ? 'review' : 'auto'}`}>
              {executionIsolation?.label || '等待目标'}
            </span>
            <small>{executionIsolation?.reason || '等待目标后自动生成隔离策略'}</small>
          </div>
          <div className="cmd-policy-tags">
            {isolationTags.length ? isolationTags.map((item) => (
              <span key={item}>{item}</span>
            )) : <em>等待目标</em>}
          </div>
          {executionIsolation?.targetMode && executionIsolation.targetMode !== executionIsolation.mode && (
            <div className="cmd-policy-note muted">
              目标模式：{executionIsolation.targetMode}；当前约束：{executionIsolation.mode}
            </div>
          )}
        </div>

        <div className="cmd-preview-block">
          <div className="cmd-block-title">治理链路</div>
          <div className="cmd-route">
            {governance.map((stage, index) => (
              <div className="cmd-route-item" key={`${stage.stage}-${index}`}>
                <div className="cmd-route-dot">{index + 1}</div>
                <div>
                  <b>{stage.dept}</b>
                  <span>{stage.label}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {lastRun && (
          <div className="cmd-result">
            <CheckCircle2 size={18} />
            <div>
              <b>{lastRun.taskId}</b>
              <span>{lastRun.id} 已保存</span>
            </div>
            <button type="button" onClick={openTask}>
              查看
            </button>
          </div>
        )}

        <div className="cmd-preview-foot">
          <Play size={14} />
          <span>系统会先生成 RunSpec；低风险自动分发，高风险命令、待配置能力和方案任务先进入审批。</span>
        </div>
      </aside>
    </div>
  );
}
