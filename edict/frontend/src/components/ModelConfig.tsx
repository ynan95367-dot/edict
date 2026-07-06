import { type FormEvent, useEffect, useMemo, useState } from 'react';
import { useStore } from '../store';
import {
  api,
  type ModelHealthAgent,
  type ModelHealthData,
  type ModelProbeData,
  type ModelRegistryData,
  type ModelRegistryEntry,
} from '../api';
import { AgentModelGrid } from './model-config/AgentModelGrid';
import { DispatchChannelPanel } from './model-config/DispatchChannelPanel';
import { ModelChangeLogPanel } from './model-config/ModelChangeLogPanel';
import { ModelHealthPanel } from './model-config/ModelHealthPanel';
import { ModelRegistryPanel } from './model-config/ModelRegistryPanel';
import { ModelSpeedPanel } from './model-config/ModelSpeedPanel';
import type { CustomModelDraft } from './model-config/ManualApiModelForm';
import { buildCurrentModelIds, buildRegistryOptions, sortRegistryModels } from './model-config/modelConfigUtils';

export default function ModelConfig() {
  const agentConfig = useStore((s) => s.agentConfig);
  const changeLog = useStore((s) => s.changeLog);
  const loadAgentConfig = useStore((s) => s.loadAgentConfig);
  const toast = useStore((s) => s.toast);

  const [selMap, setSelMap] = useState<Record<string, string>>({});
  const [statusMap, setStatusMap] = useState<Record<string, { cls: string; text: string }>>({});
  const [channelSel, setChannelSel] = useState('feishu');
  const [channelStatus, setChannelStatus] = useState('');
  const [health, setHealth] = useState<ModelHealthData | null>(null);
  const [healthError, setHealthError] = useState('');
  const [healthLoading, setHealthLoading] = useState(false);
  const [registry, setRegistry] = useState<ModelRegistryData | null>(null);
  const [registryError, setRegistryError] = useState('');
  const [registryLoading, setRegistryLoading] = useState(false);
  const [probe, setProbe] = useState<ModelProbeData | null>(null);
  const [probeError, setProbeError] = useState('');
  const [probeLoading, setProbeLoading] = useState('');
  const [modelQuery, setModelQuery] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [customModel, setCustomModel] = useState<CustomModelDraft>({
    providerId: 'openrouter',
    providerName: 'OpenRouter',
    modelId: '',
    label: '',
    apiType: 'openai',
    baseURL: '',
    apiKey: '',
  });
  const [customStatus, setCustomStatus] = useState('');

  const loadHealth = async () => {
    setHealthLoading(true);
    try {
      const data = await api.modelHealth();
      setHealth(data);
      setHealthError('');
      return data;
    } catch {
      setHealthError('模型健康接口不可达');
      return null;
    } finally {
      setHealthLoading(false);
    }
  };

  const loadRegistry = async (refresh = false) => {
    setRegistryLoading(true);
    try {
      const data = refresh ? await api.refreshModelRegistry() : await api.modelRegistry();
      setRegistry(data);
      setRegistryError(data.errors?.length ? data.errors.join('；') : '');
      if (refresh) {
        await loadAgentConfig();
        await loadHealth();
      }
      return data;
    } catch {
      setRegistryError('模型注册表接口不可达');
      return null;
    } finally {
      setRegistryLoading(false);
    }
  };

  const loadProbes = async () => {
    try {
      const data = await api.modelProbes();
      setProbe(data);
      setProbeError('');
      return data;
    } catch {
      setProbeError('模型观测接口不可达');
      return null;
    }
  };

  useEffect(() => {
    loadAgentConfig();
    loadHealth();
    loadRegistry(false);
    loadProbes();
  }, [loadAgentConfig]);

  useEffect(() => {
    const running = Boolean(probe?.running);
    const continuous = Boolean(probe?.observerRunning || probe?.config?.enabled);
    const intervalMs = running ? 3000 : continuous ? 10000 : 30000;
    const timer = window.setInterval(async () => {
      const nextProbe = await loadProbes();
      if (running || nextProbe?.running) {
        await Promise.all([loadRegistry(false), loadHealth()]);
      }
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [probe?.running, probe?.observerRunning, probe?.config?.enabled]);

  useEffect(() => {
    if (agentConfig?.agents) {
      const m: Record<string, string> = {};
      agentConfig.agents.forEach((ag) => {
        m[ag.id] = ag.model;
      });
      setSelMap(m);
    }
    if (agentConfig?.dispatchChannel) {
      setChannelSel(agentConfig.dispatchChannel);
    }
  }, [agentConfig]);

  const healthByAgent = useMemo(() => {
    const map: Record<string, ModelHealthAgent> = {};
    health?.agents?.forEach((item) => {
      map[item.agentId] = item;
    });
    return map;
  }, [health]);

  const models = useMemo(() => buildRegistryOptions(registry, agentConfig), [registry, agentConfig]);
  const registryById = useMemo(() => {
    const map: Record<string, ModelRegistryEntry> = {};
    registry?.models?.forEach((item) => {
      map[item.id] = item;
    });
    return map;
  }, [registry]);

  const visibleRegistryModels = useMemo(() => {
    const q = modelQuery.trim().toLowerCase();
    const items = [...(registry?.models || [])].filter((item) => {
      if (!q) return true;
      return [
        item.id,
        item.label,
        item.provider,
        item.providerId,
        item.tierLabel,
        ...(item.sources || []),
      ].some((value) => String(value || '').toLowerCase().includes(q));
    });
    return sortRegistryModels(items);
  }, [registry, modelQuery]);

  const currentAgentModelIds = useMemo(() => {
    return buildCurrentModelIds(agentConfig);
  }, [agentConfig]);

  if (!agentConfig?.agents) {
    return <div className="empty" style={{ gridColumn: '1/-1' }}>⚠️ 请先启动本地服务器</div>;
  }

  const isOpenCodeRuntime = (agentConfig.runtime || '').toLowerCase() === 'opencode';

  const refreshRegistry = async () => {
    await loadRegistry(true);
    await loadProbes();
    toast('OpenCode 模型已同步', 'ok');
  };

  const runProbe = async (mode: 'all' | 'visible') => {
    const modelIds = mode === 'visible' ? visibleRegistryModels.map((m) => m.id) : currentAgentModelIds;
    setProbeLoading(mode);
    try {
      const result = await api.runModelProbes({ modelIds, timeoutSec: 25 });
      if (!result.ok) {
        setProbeError(result.error || '无法启动模型观测');
        return;
      }
      if (result.probes) setProbe(result.probes);
      await loadRegistry(false);
      toast(result.started === false ? '模型观测已在运行' : `已开始观测 ${result.count || modelIds.length} 个模型`, 'ok');
    } catch {
      setProbeError('无法启动模型观测');
    } finally {
      setProbeLoading('');
    }
  };

  const toggleContinuousProbe = async () => {
    const probeShouldStop = Boolean(probe?.running || probe?.observerRunning || probe?.config?.enabled);
    setProbeLoading('continuous');
    try {
      const result = probeShouldStop
        ? await api.stopModelProbes()
        : await api.startModelProbes({
          modelIds: modelQuery.trim() ? visibleRegistryModels.map((m) => m.id) : currentAgentModelIds,
          intervalSec: 300,
          timeoutSec: 25,
        });
      if (!result.ok) {
        setProbeError(result.error || '无法切换持续观测');
        return;
      }
      if (result.probes) setProbe(result.probes);
      await loadRegistry(false);
      toast(probeShouldStop ? '模型观测已停止' : '持续观测已开启', 'ok');
    } catch {
      setProbeError('无法切换持续观测');
    } finally {
      setProbeLoading('');
    }
  };

  const submitCustom = async (event: FormEvent) => {
    event.preventDefault();
    setCustomStatus('保存中');
    try {
      const result = await api.addCustomModel(customModel);
      if (result.ok) {
        if (result.registry) setRegistry(result.registry);
        setCustomStatus(result.restartRequired ? '已保存，重启 OpenCode 后生效' : '已保存');
        setCustomModel((prev) => ({ ...prev, modelId: '', label: '', apiKey: '' }));
        toast('手动 API 模型已加入列表', 'ok');
        await loadAgentConfig();
      } else {
        setCustomStatus(result.error || '保存失败');
      }
    } catch {
      setCustomStatus('无法连接服务器');
    }
  };

  const applyModel = async (agentId: string) => {
    const model = selMap[agentId];
    if (!model) return;
    setStatusMap((p) => ({ ...p, [agentId]: { cls: 'pending', text: '⟳ 提交中…' } }));
    try {
      const result = await api.setModel(agentId, model);
      if (result.ok) {
        setStatusMap((p) => ({ ...p, [agentId]: { cls: 'ok', text: `已切换到 ${model}` } }));
        toast(`${agentId} 模型已更改`, 'ok');
        if (result.registry) setRegistry(result.registry);
        await loadAgentConfig();
        await loadHealth();
      } else {
        setStatusMap((p) => ({ ...p, [agentId]: { cls: 'err', text: result.error || '模型切换失败' } }));
      }
    } catch {
      setStatusMap((p) => ({ ...p, [agentId]: { cls: 'err', text: '无法连接服务器' } }));
    }
  };

  const resetModelSelection = (agentId: string) => {
    const ag = agentConfig.agents.find((a) => a.id === agentId);
    if (ag) setSelMap((p) => ({ ...p, [agentId]: ag.model }));
  };

  const applyDispatchChannel = async () => {
    try {
      const result = await api.setDispatchChannel(channelSel);
      if (result.ok) {
        setChannelStatus('✅ 已保存');
        toast('交办渠道已切换', 'ok');
        loadAgentConfig();
      } else {
        setChannelStatus(`❌ ${result.error || '失败'}`);
      }
    } catch {
      setChannelStatus('❌ 无法连接');
    }
    window.setTimeout(() => setChannelStatus(''), 3000);
  };

  return (
    <div>
      <ModelSpeedPanel
        agentConfig={agentConfig}
        registry={registry}
        health={health}
        probe={probe}
        registryLoading={registryLoading}
        registryError={registryError}
        probeError={probeError}
        probeLoading={probeLoading}
        modelQuery={modelQuery}
        visibleRegistryModels={visibleRegistryModels}
        onRefreshRegistry={refreshRegistry}
        onRunProbe={runProbe}
        onToggleContinuousProbe={toggleContinuousProbe}
        onModelQueryChange={setModelQuery}
      />

      <details
        className="model-advanced"
        open={advancedOpen}
        onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}
      >
        <summary>
          <span>高级设置</span>
          <b>模型分配、手动 API、原始注册表、交办渠道和历史记录</b>
          <em>{advancedOpen ? '收起' : '展开'}</em>
        </summary>

        {advancedOpen && (
          <div className="model-advanced-body">
            <ModelHealthPanel
              agentConfig={agentConfig}
              health={health}
              healthByAgent={healthByAgent}
              healthError={healthError}
              healthLoading={healthLoading}
              onRefresh={loadHealth}
            />

            <AgentModelGrid
              agentConfig={agentConfig}
              models={models}
              selMap={selMap}
              statusMap={statusMap}
              healthByAgent={healthByAgent}
              registryById={registryById}
              onSelect={(agentId, value) => setSelMap((prev) => ({ ...prev, [agentId]: value }))}
              onApply={applyModel}
              onReset={resetModelSelection}
            />

            <ModelRegistryPanel
              registry={registry}
              registryLoading={registryLoading}
              registryError={registryError}
              probe={probe}
              probeError={probeError}
              probeLoading={probeLoading}
              modelQuery={modelQuery}
              models={models}
              visibleRegistryModels={visibleRegistryModels}
              customModel={customModel}
              customStatus={customStatus}
              onRefreshRegistry={refreshRegistry}
              onRunProbe={runProbe}
              onToggleContinuousProbe={toggleContinuousProbe}
              onModelQueryChange={setModelQuery}
              onCustomChange={(key, value) => setCustomModel((prev) => ({ ...prev, [key]: value }))}
              onCustomSubmit={submitCustom}
            />

            <DispatchChannelPanel
              channelSel={channelSel}
              currentChannel={agentConfig.dispatchChannel}
              channelStatus={channelStatus}
              isOpenCodeRuntime={isOpenCodeRuntime}
              onSelect={setChannelSel}
              onApply={applyDispatchChannel}
            />

            <ModelChangeLogPanel changeLog={changeLog} />
          </div>
        )}
      </details>
    </div>
  );
}
