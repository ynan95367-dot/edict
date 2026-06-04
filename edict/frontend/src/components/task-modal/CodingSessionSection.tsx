import type { CodingSessionData } from '../../api';
import { CodingSessionLists } from './CodingSessionLists';
import { CheckpointStrip, IsolationHealthStrip, IsolationStrip } from './IsolationPanels';
import { PatchReviewPanel } from './PatchReviewPanel';
import { sessionStatusLabel, shortTrace } from './codingSessionUtils';

type CodingSessionSectionProps = {
  data: CodingSessionData | null;
  onOpenSource: (path: string, startLine?: number, endLine?: number) => void;
  onCreatePatch: (paths: string[]) => void;
  onDecidePatch: (patchId: string, action: 'approve' | 'reject') => void;
};

export function CodingSessionSection({
  data,
  onOpenSource,
  onCreatePatch,
  onDecidePatch,
}: CodingSessionSectionProps) {
  if (!data?.ok) return null;
  const s = data.summary;
  const runtimeSession = data.runtimeSession || {};
  const sessionStatus = runtimeSession.status || (data.sessionId ? 'bound' : 'unbound');
  const sessionTone = sessionStatus === 'trace-mismatch' ? 'err' : sessionStatus === 'bound' || sessionStatus === 'observed' ? 'ok' : 'warn';

  return (
    <div className="cockpit">
      <div className="cockpit-head">
        <div>
          <div className="cockpit-title">执行证据</div>
          <div className="cockpit-sub">
            {data.runtime || 'runtime'} · session <span className={`mono tone-${sessionTone}`}>{data.sessionId ? shortTrace(data.sessionId) : '未绑定'}</span>
            {data.traceId && <span> · trace <span className="mono">{shortTrace(data.traceId)}</span></span>}
          </div>
        </div>
        <div className={`cockpit-mode ${s.hasPatchReview ? 'ok' : 'warn'}`}>
          {sessionStatusLabel(sessionStatus)} · {s.hasPatchReview ? `Patch 审批已接入${s.pendingPatchCount ? ` · 待审 ${s.pendingPatchCount}` : ''}` : '尚未接入 Patch 审批'}
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

      <IsolationHealthStrip health={data.isolationHealth} />
      <IsolationStrip isolation={data.executionIsolation} />
      <CheckpointStrip checkpoint={data.checkpoint} />

      <PatchReviewPanel data={data} onCreatePatch={onCreatePatch} onDecidePatch={onDecidePatch} />
      <CodingSessionLists data={data} onOpenSource={onOpenSource} />

      {!!data.missingLayers.length && (
        <div className="cockpit-gap">
          待补：{data.missingLayers.join(' · ')}
        </div>
      )}
    </div>
  );
}
