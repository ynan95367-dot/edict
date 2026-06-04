import { Download, ExternalLink, FileText, Package } from 'lucide-react';
import { api } from '../../api';
import { formatDashboardDateTime } from '../../time';
import type { OutputGroup } from '../../api';

export function TaskOutputSection({ group, outputText }: { group?: OutputGroup | null; outputText?: string }) {
  const note = group?.outputText || (outputText && outputText !== '-' ? outputText : '');
  const files = (group?.files || []).slice(0, 6);
  if (!note && !files.length) return null;

  return (
    <div className="task-output-section">
      <div className="task-output-head">
        <div>
          <div className="task-output-title"><Package size={14} />本任务产物</div>
          <div className="task-output-sub">
            {files.length ? `${group?.files?.length || files.length} 个文件` : '无文件'}{group?.updatedAt ? ` · ${formatDashboardDateTime(group.updatedAt)}` : ''}
          </div>
        </div>
        {group?.taskId && <span className="task-output-tag">{group.taskId}</span>}
      </div>
      {note && <div className="task-output-note">{note}</div>}
      {!!files.length && (
        <div className="task-output-list">
          {files.map((file) => (
            <div className="task-output-card" key={file.path}>
              <span className="task-output-icon"><FileText size={15} /></span>
              <span className="task-output-main">
                <b>{file.name}</b>
                <em>{file.kind} · {file.source} · {file.sizeLabel}</em>
                <code>{file.path}</code>
              </span>
              <span className="task-output-actions">
                <a href={api.outputFileUrl(file.path)} target="_blank" rel="noreferrer" title="打开产物">
                  <ExternalLink size={14} />
                </a>
                <a href={api.outputFileUrl(file.path, true)} title="下载产物">
                  <Download size={14} />
                </a>
              </span>
            </div>
          ))}
          {(group?.files?.length || 0) > files.length && (
            <div className="task-output-more">还有 {(group?.files?.length || 0) - files.length} 个文件，可到输出文件页查看</div>
          )}
        </div>
      )}
    </div>
  );
}
