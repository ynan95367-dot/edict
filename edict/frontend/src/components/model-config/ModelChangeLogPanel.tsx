import type { ChangeLogEntry } from '../../api';

type ModelChangeLogPanelProps = {
  changeLog?: ChangeLogEntry[];
};

export function ModelChangeLogPanel({ changeLog }: ModelChangeLogPanelProps) {
  return (
    <div style={{ marginTop: 24 }}>
      <div className="sec-title">变更日志</div>
      <div className="cl-list">
        {!changeLog?.length ? (
          <div style={{ fontSize: 12, color: 'var(--muted)', padding: '8px 0' }}>暂无变更</div>
        ) : (
          [...changeLog]
            .reverse()
            .slice(0, 15)
            .map((e, i) => (
              <div className="cl-row" key={`${e.at}-${e.agentId}-${i}`}>
                <span className="cl-t">{(e.at || '').substring(0, 16).replace('T', ' ')}</span>
                <span className="cl-a">{e.agentId}</span>
                <span className="cl-c">
                  <b>{e.oldModel}</b> → <b>{e.newModel}</b>
                  {e.rolledBack && (
                    <span
                      style={{
                        color: 'var(--danger)',
                        fontSize: 10,
                        border: '1px solid #ff527044',
                        padding: '1px 5px',
                        borderRadius: 3,
                        marginLeft: 4,
                      }}
                    >
                      ⚠ 已回滚
                    </span>
                  )}
                </span>
              </div>
            ))
        )}
      </div>
    </div>
  );
}
