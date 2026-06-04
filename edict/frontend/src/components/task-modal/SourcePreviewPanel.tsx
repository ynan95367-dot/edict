import { ExternalLink } from 'lucide-react';
import type { SourceFileResult } from '../../api';

type SourcePreviewPanelProps = {
  data: SourceFileResult;
  onClose: () => void;
  onOpenEditor: (path: string, startLine?: number) => void;
};

export function SourcePreviewPanel({ data, onClose, onOpenEditor }: SourcePreviewPanelProps) {
  if (!data.ok) {
    return (
      <div className="source-preview">
        <div className="sp-head">
          <div className="sp-title">源码片段</div>
          <button className="sp-close" onClick={onClose}>关闭</button>
        </div>
        <div className="cockpit-empty">{data.error || '无法读取文件'}</div>
      </div>
    );
  }

  return (
    <div className="source-preview">
      <div className="sp-head">
        <div>
          <div className="sp-title">源码片段</div>
          <div className="sp-path">{data.path} · {data.viewStart}-{data.viewEnd}/{data.totalLines}</div>
        </div>
        <div className="sp-actions">
          <button
            className="sp-open"
            onClick={() => onOpenEditor(data.path, data.startLine || data.viewStart)}
            title="在本机编辑器打开"
          >
            <ExternalLink size={13} />
            打开编辑器
          </button>
          <button className="sp-close" onClick={onClose}>关闭</button>
        </div>
      </div>
      <pre className="sp-code">
        {data.lines.map((line) => (
          <div className={`sp-line${line.highlight ? ' hl' : ''}`} key={line.no}>
            <span className="sp-no">{line.no}</span>
            <code className="sp-text">{line.text || ' '}</code>
          </div>
        ))}
      </pre>
    </div>
  );
}
