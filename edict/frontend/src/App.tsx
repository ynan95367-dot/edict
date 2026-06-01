import { useEffect } from 'react';
import {
  AlertTriangle,
  Bot,
  Command,
  Compass,
  FileText,
  Landmark,
  Library,
  MessageSquare,
  Newspaper,
  Package,
  RefreshCw,
  ScrollText,
  Target,
  Users,
  Workflow,
  type LucideIcon,
} from 'lucide-react';
import { useStore, TAB_DEFS, startPolling, stopPolling, isEdict, isArchived } from './store';
import EdictBoard from './components/EdictBoard';
import MonitorPanel from './components/MonitorPanel';
import OfficialPanel from './components/OfficialPanel';
import ModelConfig from './components/ModelConfig';
import SkillsConfig from './components/SkillsConfig';
import SessionsPanel from './components/SessionsPanel';
import OutputPanel from './components/OutputPanel';
import CommandCenter from './components/CommandCenter';
import MemorialPanel from './components/MemorialPanel';
import TemplatePanel from './components/TemplatePanel';
import MorningPanel from './components/MorningPanel';
import TaskModal from './components/TaskModal';
// ConfirmDialog is used inside TaskModal as needed
import Toaster from './components/Toaster';
import CourtCeremony from './components/CourtCeremony';
import CourtDiscussion from './components/CourtDiscussion';

const TAB_ICONS: Record<string, LucideIcon> = {
  command: Command,
  edicts: ScrollText,
  court: Landmark,
  monitor: Workflow,
  officials: Users,
  models: Bot,
  skills: Target,
  sessions: MessageSquare,
  outputs: Package,
  memorials: FileText,
  templates: Library,
  morning: Newspaper,
};

export default function App() {
  const activeTab = useStore((s) => s.activeTab);
  const setActiveTab = useStore((s) => s.setActiveTab);
  const liveStatus = useStore((s) => s.liveStatus);
  const runtimeOutbox = useStore((s) => s.runtimeOutbox);
  const countdown = useStore((s) => s.countdown);
  const loadAll = useStore((s) => s.loadAll);

  useEffect(() => {
    startPolling();
    return () => stopPolling();
  }, []);

  // Compute header chips
  const tasks = liveStatus?.tasks || [];
  const edicts = tasks.filter(isEdict);
  const activeEdicts = edicts.filter((t) => !isArchived(t));
  const sync = liveStatus?.syncStatus;
  const syncOk = sync?.ok;
  const queueTone = runtimeOutbox?.failed
    ? 'err'
    : runtimeOutbox?.pending || runtimeOutbox?.running
      ? 'warn'
      : 'ok';

  // Tab badge counts
  const tabBadge = (key: string): string => {
    if (key === 'edicts') return String(activeEdicts.length);
    if (key === 'sessions') return String(tasks.filter((t) => !isEdict(t)).length);
    if (key === 'memorials') return String(edicts.filter((t) => ['Done', 'Cancelled'].includes(t.state)).length);
    if (key === 'monitor') {
      const activeDepts = tasks.filter((t) => isEdict(t) && t.state === 'Doing').length;
      return activeDepts + '活跃';
    }
    return '';
  };

  return (
    <div className="wrap">
      {/* ── Header ── */}
      <div className="hdr">
        <div>
          <div className="logo">三省六部 · 总控台</div>
          <div className="sub-text">Sansheng-Liubu Agent Dashboard</div>
        </div>
        <div className="hdr-r">
          <span className={`chip ${syncOk ? 'ok' : syncOk === false ? 'err' : ''}`}>
            <span className={`status-dot ${syncOk ? 'ok' : syncOk === false ? 'err' : 'warn'}`} />
            {syncOk ? '同步正常' : syncOk === false ? '服务器未启动' : '连接中'}
          </span>
          <span className="chip">{activeEdicts.length} 道旨意</span>
          <span className={`chip ${queueTone}`}>
            <AlertTriangle size={12} />
            队列 {runtimeOutbox?.pending || 0}/{runtimeOutbox?.running || 0}/{runtimeOutbox?.failed || 0}
          </span>
          <button className="btn-refresh" onClick={() => loadAll()} aria-label="刷新看板">
            <RefreshCw size={14} />
            刷新
          </button>
          <span className="poll-chip"><RefreshCw size={12} /> {countdown}s</span>
        </div>
      </div>

      {/* ── Tabs ── */}
      <div className="tabs">
        {TAB_DEFS.map((t) => {
          const Icon = TAB_ICONS[t.key] || Compass;
          return (
            <button
              key={t.key}
              className={`tab ${activeTab === t.key ? 'active' : ''}`}
              onClick={() => setActiveTab(t.key)}
              type="button"
            >
              <Icon className="tab-ico" size={15} />
              {t.label}
              {tabBadge(t.key) && <span className="tbadge">{tabBadge(t.key)}</span>}
            </button>
          );
        })}
      </div>

      {/* ── Panels ── */}
      {activeTab === 'command' && <CommandCenter />}
      {activeTab === 'edicts' && <EdictBoard />}
      {activeTab === 'court' && <CourtDiscussion />}
      {activeTab === 'monitor' && <MonitorPanel />}
      {activeTab === 'officials' && <OfficialPanel />}
      {activeTab === 'models' && <ModelConfig />}
      {activeTab === 'skills' && <SkillsConfig />}
      {activeTab === 'sessions' && <SessionsPanel />}
      {activeTab === 'outputs' && <OutputPanel />}
      {activeTab === 'memorials' && <MemorialPanel />}
      {activeTab === 'templates' && <TemplatePanel />}
      {activeTab === 'morning' && <MorningPanel />}

      {/* ── Overlays ── */}
      <TaskModal />
      <Toaster />
      <CourtCeremony />
    </div>
  );
}
