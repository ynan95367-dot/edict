import type { FormEvent } from 'react';

export type CustomModelDraft = {
  providerId: string;
  providerName: string;
  modelId: string;
  label: string;
  apiType: string;
  baseURL: string;
  apiKey: string;
};

type ManualApiModelFormProps = {
  customModel: CustomModelDraft;
  customStatus: string;
  onChange: (key: keyof CustomModelDraft, value: string) => void;
  onSubmit: (event: FormEvent) => void;
};

export function ManualApiModelForm({ customModel, customStatus, onChange, onSubmit }: ManualApiModelFormProps) {
  return (
    <form className="mr-custom" onSubmit={onSubmit}>
      <div className="mr-custom-title">手动 API 模型</div>
      <div className="mr-form-grid">
        <label>
          <span>Provider ID</span>
          <input value={customModel.providerId} onChange={(e) => onChange('providerId', e.target.value)} placeholder="openrouter" />
        </label>
        <label>
          <span>Provider 名称</span>
          <input value={customModel.providerName} onChange={(e) => onChange('providerName', e.target.value)} placeholder="OpenRouter" />
        </label>
        <label className="wide">
          <span>模型 ID</span>
          <input value={customModel.modelId} onChange={(e) => onChange('modelId', e.target.value)} placeholder="anthropic/claude-3.5-sonnet 或 gpt-4.1" />
        </label>
        <label>
          <span>API 类型</span>
          <select value={customModel.apiType} onChange={(e) => onChange('apiType', e.target.value)}>
            <option value="openai">OpenAI Compatible</option>
            <option value="anthropic">Anthropic</option>
            <option value="google">Google</option>
            <option value="custom">Custom</option>
          </select>
        </label>
        <label>
          <span>显示名</span>
          <input value={customModel.label} onChange={(e) => onChange('label', e.target.value)} placeholder="可留空" />
        </label>
        <label className="wide">
          <span>Base URL</span>
          <input value={customModel.baseURL} onChange={(e) => onChange('baseURL', e.target.value)} placeholder="https://api.openrouter.ai/v1" />
        </label>
        <label className="wide">
          <span>API Key</span>
          <input type="password" value={customModel.apiKey} onChange={(e) => onChange('apiKey', e.target.value)} placeholder="留空则保留已有密钥" />
        </label>
      </div>
      <div className="mr-custom-actions">
        <button className="btn btn-p" type="submit" disabled={!customModel.modelId.trim()}>
          保存模型
        </button>
        {customStatus && <span>{customStatus}</span>}
      </div>
    </form>
  );
}
