type GovernanceStage = {
  key: string;
  status: string;
  icon: string;
  dept: string;
  action: string;
};

type GovernanceMapProps = {
  stages: GovernanceStage[];
  completedStageCount: number;
};

export function GovernanceMap({ stages, completedStageCount }: GovernanceMapProps) {
  return (
    <div className="governance-map">
      <div className="section-headline">
        <div>
          <span>从下旨到回奏</span>
          <b>治理链路</b>
        </div>
        <em>{completedStageCount}/{stages.length} 已完成</em>
      </div>
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
    </div>
  );
}
