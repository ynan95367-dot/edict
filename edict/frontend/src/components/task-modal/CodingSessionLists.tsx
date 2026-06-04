import { api } from '../../api';
import type { CodingEvent, CodingSessionData } from '../../api';
import { codingKindIcon, codingKindLabel, fmtActivityTime, lineLabel, shortPath } from './codingSessionUtils';

type CodingSessionListsProps = {
  data: CodingSessionData;
  onOpenSource: (path: string, startLine?: number, endLine?: number) => void;
};

export function CodingSessionLists({ data, onOpenSource }: CodingSessionListsProps) {
  const recent = data.events.slice(-12).reverse();
  const filePreview = data.files.slice(0, 5);
  const outputPreview = data.outputs.slice(0, 4);
  const commandPreview = [...data.commands, ...data.tests].slice(-4).reverse();

  return (
    <>
      <div className="cockpit-columns">
        <div className="cockpit-panel">
          <div className="cockpit-label">文件与产物</div>
          {!filePreview.length && !outputPreview.length ? (
            <div className="cockpit-empty">暂无文件事件</div>
          ) : (
            <>
              {outputPreview.map((e) => <CodingFileRow key={`out-${e.path || e.title}`} event={e} />)}
              {filePreview.map((f) => (
                <div className="cockpit-row" key={f.path}>
                  <span className="cockpit-row-icon">📄</span>
                  {f.sourceUrl ? (
                    <button
                      className="cockpit-row-main link as-button"
                      onClick={() => onOpenSource(f.path, f.lastStartLine || 0, f.lastEndLine || 0)}
                    >
                      {shortPath(f.path)}
                    </button>
                  ) : (
                    <span className="cockpit-row-main">{shortPath(f.path)}</span>
                  )}
                  <span className="cockpit-row-meta">
                    {lineLabel(f.lastStartLine, f.lastEndLine) || `读 ${f.reads} · 改 ${f.changes} · 出 ${f.outputs}`}
                  </span>
                </div>
              ))}
            </>
          )}
        </div>

        <div className="cockpit-panel">
          <div className="cockpit-label">命令与测试</div>
          {!commandPreview.length ? (
            <div className="cockpit-empty">暂无命令事件</div>
          ) : (
            commandPreview.map((e) => (
              <div className="cockpit-row" key={`${e.kind}-${e.at}-${e.title}`}>
                <span className="cockpit-row-icon">{codingKindIcon(e.kind)}</span>
                <span className="cockpit-row-main">{e.command || e.title}</span>
                <span className={`cockpit-row-meta ${e.status === 'fail' ? 'err' : e.status === 'pass' ? 'ok' : ''}`}>{codingKindLabel(e.kind)}</span>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="cockpit-events">
        <div className="cockpit-label">最近执行事件</div>
        {!recent.length ? (
          <div className="cockpit-empty">暂无事件</div>
        ) : (
          recent.map((e) => (
            <CodingEventRow
              key={`${e.kind}-${e.at}-${e.title}-${e.path}`}
              event={e}
              onOpenSource={onOpenSource}
            />
          ))
        )}
      </div>
    </>
  );
}

function CodingFileRow({ event }: { event: CodingEvent }) {
  const file = event.meta || {};
  const path = event.path || String(file.path || '');
  const href = path ? api.outputFileUrl(path) : '';
  return (
    <div className="cockpit-row">
      <span className="cockpit-row-icon">📦</span>
      {href ? (
        <a className="cockpit-row-main link" href={href} target="_blank" rel="noreferrer">{shortPath(path)}</a>
      ) : (
        <span className="cockpit-row-main">{event.title}</span>
      )}
      <span className="cockpit-row-meta">{event.status || 'ready'}</span>
    </div>
  );
}

function CodingEventRow({
  event,
  onOpenSource,
}: {
  event: CodingEvent;
  onOpenSource: (path: string, startLine?: number, endLine?: number) => void;
}) {
  const main = event.path ? shortPath(event.path) : event.command || event.title;
  const canOpen = !!event.sourceUrl && !!event.path;
  return (
    <div className="cockpit-event">
      <span className="cockpit-event-time">{fmtActivityTime(event.at)}</span>
      <span className="cockpit-event-kind">{codingKindIcon(event.kind)} {codingKindLabel(event.kind)}</span>
      {canOpen ? (
        <button
          className="cockpit-event-main link as-button"
          onClick={() => onOpenSource(event.path, event.startLine, event.endLine)}
        >
          {main}{lineLabel(event.startLine, event.endLine) ? ` · ${lineLabel(event.startLine, event.endLine)}` : ''}
        </button>
      ) : (
        <span className="cockpit-event-main">{main}</span>
      )}
      {event.detail && <span className="cockpit-event-detail">{event.detail}</span>}
    </div>
  );
}
