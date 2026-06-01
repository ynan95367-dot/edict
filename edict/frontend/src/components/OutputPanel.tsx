import { useEffect, useMemo, useState } from 'react';
import {
  Copy,
  Database,
  Download,
  ExternalLink,
  FileArchive,
  FileText,
  Image as ImageIcon,
  Package,
  RefreshCw,
  Search,
  Video,
} from 'lucide-react';
import { api, type OutputFile, type OutputGroup } from '../api';
import { useStore } from '../store';

const kindIcon = (kind: string) => {
  if (kind === '图片') return <ImageIcon size={16} />;
  if (kind === '数据') return <Database size={16} />;
  if (kind === '办公文件') return <FileArchive size={16} />;
  if (kind === '网页产物') return <Package size={16} />;
  if (kind === '视频') return <Video size={16} />;
  return <FileText size={16} />;
};

const timeLabel = (value: string) => {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value.replace('T', ' ');
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

export default function OutputPanel() {
  const toast = useStore((s) => s.toast);
  const [files, setFiles] = useState<OutputFile[]>([]);
  const [groups, setGroups] = useState<OutputGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState('全部');

  const load = async () => {
    setLoading(true);
    try {
      const res = await api.outputFiles();
      setFiles(res.files || []);
      setGroups(res.groups || []);
    } catch {
      toast('输出文件读取失败', 'err');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const kinds = useMemo(() => ['全部', ...Array.from(new Set(files.map((f) => f.kind))).sort()], [files]);
  const filteredGroups = useMemo(() => {
    const q = query.trim().toLowerCase();
    return groups
      .map((group) => {
        const groupMatches = !q || [group.taskId, group.taskTitle, group.state, group.org, group.outputText]
          .filter(Boolean)
          .some((part) => String(part).toLowerCase().includes(q));
        const matchedFiles = group.files.filter((f) => {
          if (kind !== '全部' && f.kind !== kind) return false;
          if (!q || groupMatches) return true;
          return [f.name, f.path, f.taskId, f.taskTitle, f.source]
            .filter(Boolean)
            .some((part) => String(part).toLowerCase().includes(q));
        });
        const outputText = kind === '全部' && group.outputText && groupMatches ? group.outputText : '';
        return { ...group, files: matchedFiles, outputText };
      })
      .filter((group) => group.files.length || group.outputText);
  }, [groups, kind, query]);

  const copyPath = (file: OutputFile) => {
    navigator.clipboard.writeText(file.absolutePath || file.path).then(
      () => toast('路径已复制', 'ok'),
      () => toast('复制失败', 'err')
    );
  };

  return (
    <div>
      <div className="out-head">
        <div>
          <div className="out-title">输出文件</div>
          <div className="out-sub">{groups.length} 个任务组 · {files.length} 个文件</div>
        </div>
        <button className="btn btn-g out-refresh" onClick={load} disabled={loading}>
          <RefreshCw size={14} /> {loading ? '刷新中' : '刷新'}
        </button>
      </div>

      <div className="out-tools">
        <div className="out-search">
          <Search size={15} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索文件名、路径、任务" />
        </div>
        <div className="out-kinds">
          {kinds.map((k) => (
            <button key={k} className={`sess-filter${kind === k ? ' active' : ''}`} onClick={() => setKind(k)}>
              {k}
            </button>
          ))}
        </div>
      </div>

      <div className="out-list">
        {loading ? (
          <div className="out-empty">正在读取输出文件…</div>
        ) : !filteredGroups.length ? (
          <div className="out-empty">暂无匹配文件</div>
        ) : (
          filteredGroups.map((group) => (
            <div className="out-group" key={group.taskId}>
              <div className="out-group-head">
                <div>
                  <div className="out-group-title">{group.taskTitle}</div>
                  <div className="out-group-meta">
                    {group.taskId !== '__unassigned__' && <span>{group.taskId}</span>}
                    {group.state && <span>{group.state}</span>}
                    {group.org && <span>{group.org}</span>}
                    {group.updatedAt && <span>{timeLabel(group.updatedAt)}</span>}
                  </div>
                </div>
                <div className="out-count">{group.files.length} 个文件</div>
              </div>
              {group.outputText && <div className="out-note">{group.outputText}</div>}
              <div className="out-group-files">
                {group.files.map((file) => (
                  <div className="out-card" key={file.path}>
                    <div className="out-icon">{kindIcon(file.kind)}</div>
                    <div className="out-main">
                      <div className="out-name">{file.name}</div>
                      <div className="out-meta">
                        <span>{file.kind}</span>
                        <span>{file.source}</span>
                        <span>{file.sizeLabel}</span>
                        <span>{timeLabel(file.mtime)}</span>
                      </div>
                      <div className="out-path">{file.path}</div>
                    </div>
                    <div className="out-actions">
                      <a className="icon-btn" href={api.outputFileUrl(file.path)} target="_blank" rel="noreferrer" title="打开">
                        <ExternalLink size={15} />
                      </a>
                      <a className="icon-btn" href={api.outputFileUrl(file.path, true)} title="下载">
                        <Download size={15} />
                      </a>
                      <button className="icon-btn" onClick={() => copyPath(file)} title="复制路径">
                        <Copy size={15} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
