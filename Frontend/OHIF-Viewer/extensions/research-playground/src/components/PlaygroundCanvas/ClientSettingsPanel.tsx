import React, { useState, useEffect, useCallback } from 'react';
import config from '../../config';
import { Settings, X, Save, RefreshCw, Eye, EyeOff } from 'lucide-react';

const SettingsIcon: any = Settings;
const XIcon: any = X;
const SaveIcon: any = Save;
const RefreshCwIcon: any = RefreshCw;
const EyeIcon: any = Eye;
const EyeOffIcon: any = EyeOff;

const API_BASE = config.AGENTS_API;

type Provider = 'openai' | 'azure_openai' | 'ollama';

interface FormState {
    provider: Provider;
    model: string;
    api_key: string;
    base_url: string;
    azure_endpoint: string;
    azure_deployment: string;
    api_version: string;
    vision: boolean;
    function_calling: boolean;
    json_output: boolean;
    structured_output: boolean;
    temperature: string;
    max_tokens: string;
    top_p: string;
}

const DEFAULTS: FormState = {
    provider: 'ollama',
    model: 'deepseek-v3.1:671b-cloud',
    api_key: '',
    base_url: '',
    azure_endpoint: '',
    azure_deployment: '',
    api_version: '2024-06-01',
    vision: false,
    function_calling: true,
    json_output: true,
    structured_output: true,
    temperature: '',
    max_tokens: '',
    top_p: '',
};

interface ClientSettingsPanelProps {
    isOpen: boolean;
    onClose: () => void;
}

export default function ClientSettingsPanel({ isOpen, onClose }: ClientSettingsPanelProps) {
    const [form, setForm] = useState<FormState>(DEFAULTS);
    const [saving, setSaving] = useState(false);
    const [status, setStatus] = useState<{ ok: boolean; msg: string } | null>(null);
    const [showKey, setShowKey] = useState(false);
    const [dirtyFields, setDirtyFields] = useState<Set<string>>(new Set());
    const [isTesting, setIsTesting] = useState(false);
    const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

    // Load current config on mount
    const loadConfig = useCallback(async () => {
        try {
            const res = await fetch(`${API_BASE}/client-config`);
            if (!res.ok) return;
            const resData = await res.json();
            const data = resData.config || resData; // Support both old and new format temporarily if needed
            setForm({
                provider: data.provider || 'ollama',
                model: data.model || '',
                api_key: data._masked ? '***' : '', // Show *** if masked, empty otherwise
                base_url: data.base_url || '',
                azure_endpoint: data.azure_endpoint || '',
                azure_deployment: data.azure_deployment || '',
                api_version: data.api_version || '2024-06-01',
                vision: data.vision ?? false,
                function_calling: data.function_calling ?? true,
                json_output: data.json_output ?? true,
                structured_output: data.structured_output ?? true,
                temperature: data.temperature != null ? String(data.temperature) : '',
                max_tokens: data.max_tokens != null ? String(data.max_tokens) : '',
                top_p: data.top_p != null ? String(data.top_p) : '',
            });
            setDirtyFields(new Set());
        } catch {
            // backend not reachable
        }
    }, []);

    useEffect(() => {
        if (isOpen) loadConfig();
    }, [isOpen, loadConfig]);

    const buildPayload = () => {
        const payload: any = {};
        dirtyFields.forEach(field => {
            let value = form[field as keyof FormState];
            
            if (field === 'api_key') {
                if (value && value !== '***') {
                    payload.api_key = value;
                } else if (value === '') {
                    payload.api_key = null;
                }
                return;
            }

            if (['temperature', 'top_p'].includes(field)) {
                payload[field] = value !== '' ? parseFloat(value as string) : null;
            } else if (field === 'max_tokens') {
                payload[field] = value !== '' ? parseInt(value as string, 10) : null;
            } else if (value === '' || value === null || value === undefined) {
                payload[field] = null;
            } else {
                payload[field] = value;
            }
        });
        return payload;
    };

    const handleSave = async () => {
        const payload = buildPayload();
        if (Object.keys(payload).length === 0) {
            setStatus({ ok: true, msg: 'No changes to save.' });
            return;
        }

        setSaving(true);
        setStatus(null);
        try {
            const res = await fetch(`${API_BASE}/client-config`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            const config = data.config || data;
            setStatus({ ok: true, msg: `Saved: ${config.provider} / ${config.model}` });
            setDirtyFields(new Set());
        } catch (e: any) {
            setStatus({ ok: false, msg: e.message || 'Failed to save' });
        } finally {
            setSaving(false);
        }
    };

    const handleTestConnection = async () => {
        setIsTesting(true);
        setTestResult(null);
        const payload = buildPayload();
        
        try {
            const response = await fetch(`${API_BASE}/api/test-client-config`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const result = await response.json();
            setTestResult({
                success: response.ok,
                message: result.message || (response.ok ? '✅ Connection successful!' : '❌ Connection failed.'),
            });
        } catch (error) {
            setTestResult({ success: false, message: '❌ Network error. Please try again.' });
        } finally {
            setIsTesting(false);
        }
    };

    const update = (key: keyof FormState, val: any) => {
        setForm(prev => ({ ...prev, [key]: val }));
        setDirtyFields(prev => new Set(prev).add(key));
    };

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
            <div className="bg-[#0b1015] border border-[#3c5d80] rounded-2xl w-[520px] max-h-[95vh] flex flex-col shadow-2xl">
                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-[#1f2d40]">
                    <div className="flex items-center gap-3">
                        <SettingsIcon size={18} className="text-[#5acce6]" />
                        <span className="font-bold text-white text-sm tracking-wide">Model Configuration</span>
                    </div>
                    <button onClick={onClose} className="p-1 hover:bg-[#1f2d40] rounded transition-colors">
                        <XIcon size={16} className="text-gray-400" />
                    </button>
                </div>

                {/* Body */}
                <div className="flex-1 overflow-y-auto ohif-scrollbar px-6 py-4 space-y-3.5">
                    {/* Provider */}
                    <div>
                        <label className="block text-[10px] text-gray-500 uppercase tracking-wider mb-1.5 font-bold">Provider</label>
                        <div className="flex gap-2">
                            {(['openai', 'azure_openai', 'ollama'] as Provider[]).map(p => (
                                <button
                                    key={p}
                                    onClick={() => update('provider', p)}
                                    className={`flex-1 px-3 py-2 rounded-lg text-xs font-medium border transition-all ${
                                        form.provider === p
                                            ? 'bg-[#5acce6]/10 border-[#5acce6] text-[#5acce6]'
                                            : 'bg-[#151a1f] border-[#3c5d80]/40 text-gray-400 hover:text-white hover:border-[#3c5d80]'
                                    }`}
                                >
                                    {p === 'openai' ? 'OpenAI' : p === 'azure_openai' ? 'Azure OpenAI' : 'Ollama'}
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* Model */}
                    <Field label="Model" placeholder={form.provider === 'openai' ? 'gpt-4o' : form.provider === 'azure_openai' ? 'gpt-4o' : 'deepseek-v3.1:671b-cloud'} value={form.model} onChange={v => update('model', v)} />

                    {/* API Key */}
                    <div>
                        <label className="block text-[10px] text-gray-500 uppercase tracking-wider mb-1.5 font-bold">
                            API Key {form.provider === 'ollama' && <span className="text-gray-600 normal-case">(optional)</span>}
                        </label>
                        <div className="relative">
                            <input
                                type={showKey ? 'text' : 'password'}
                                value={form.api_key}
                                onChange={e => update('api_key', e.target.value)}
                                placeholder="sk-..."
                                className="w-full bg-[#151a1f] border border-[#3c5d80]/40 rounded-lg px-3 py-2 text-xs text-white placeholder-gray-600 focus:outline-none focus:border-[#5acce6] pr-9"
                            />
                            <button
                                type="button"
                                onClick={() => setShowKey(!showKey)}
                                className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
                            >
                                {showKey ? <EyeOffIcon size={14} /> : <EyeIcon size={14} />}
                            </button>
                        </div>
                    </div>

                    {/* Base URL — show for OpenAI and Ollama */}
                    {(form.provider === 'openai' || form.provider === 'ollama') && ( 
                        <Field 
                            label="Base URL" 
                            placeholder={form.provider === 'ollama' ? 'Set REACT_APP_OLLAMA_BASE_URL or leave blank' : 'https://api.openai.com/v1'} 
                            value={form.base_url} 
                            onChange={v => update('base_url', v)} 
                            hint={form.provider === 'ollama' ? 'Defaults to REACT_APP_OLLAMA_BASE_URL if set' : 'Leave blank for default OpenAI endpoint'} 
                        /> 
                    )}

                    {/* Azure-specific fields */}
                    {form.provider === 'azure_openai' && (
                        <>
                            <Field label="Azure Endpoint" placeholder="https://your-resource.openai.azure.com/" value={form.azure_endpoint} onChange={v => update('azure_endpoint', v)} />
                            <Field label="Azure Deployment" placeholder="your-deployment-name" value={form.azure_deployment} onChange={v => update('azure_deployment', v)} />
                            <Field label="API Version" placeholder="2024-06-01" value={form.api_version} onChange={v => update('api_version', v)} />
                        </>
                    )}

                    {/* Divider */}
                    <div className="border-t border-[#1f2d40] pt-3">
                        <h4 className="text-[10px] text-gray-500 uppercase tracking-wider font-bold mb-2">Generation Parameters</h4>
                        <div className="grid grid-cols-3 gap-3">
                            <Field label="Temperature" placeholder="0.7" value={form.temperature} onChange={v => update('temperature', v)} small />
                            <Field label="Max Tokens" placeholder="4096" value={form.max_tokens} onChange={v => update('max_tokens', v)} small />
                            <Field label="Top P" placeholder="1.0" value={form.top_p} onChange={v => update('top_p', v)} small />
                        </div>
                    </div>

                    {/* Capabilities */}
                    <div className="border-t border-[#1f2d40] pt-3">
                        <h4 className="text-[10px] text-gray-500 uppercase tracking-wider font-bold mb-2">Model Capabilities</h4>
                        <div className="grid grid-cols-2 gap-2">
                            <Toggle label="Function Calling" checked={form.function_calling} onChange={v => update('function_calling', v)} />
                            <Toggle label="JSON Output" checked={form.json_output} onChange={v => update('json_output', v)} />
                            <Toggle label="Vision" checked={form.vision} onChange={v => update('vision', v)} />
                            <Toggle label="Structured Output" checked={form.structured_output} onChange={v => update('structured_output', v)} />
                        </div>
                    </div>
                </div>

                {/* Footer */}
                <div className="px-6 py-4 border-t border-[#1f2d40] flex flex-col gap-2">
                    <div className="flex items-center justify-between">
                        {status && (
                            <span className={`text-xs ${status.ok ? 'text-[#6fe5b8]' : 'text-[#ff6b6b]'}`}>
                                {status.msg}
                            </span>
                        )}
                        {!status && <span />}
                        <div className="flex gap-2">
                            <button
                                onClick={loadConfig}
                                className="px-3 py-1.5 bg-[#151a1f] text-gray-400 border border-[#3c5d80]/40 rounded-lg text-xs hover:text-white transition-colors flex items-center gap-1.5"
                            >
                                <RefreshCwIcon size={12} /> Reset
                            </button>
                            <button
                                onClick={handleTestConnection}
                                disabled={isTesting}
                                className="px-3 py-1.5 bg-[#3c5d80] text-white rounded-lg text-xs font-bold hover:bg-[#4a729c] transition-colors flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed"
                            >
                                {isTesting ? 'Testing...' : 'Test Connection'}
                            </button>
                            <button
                                onClick={handleSave}
                                disabled={saving || !form.model}
                                className="px-4 py-1.5 bg-[#5acce6] text-black rounded-lg text-xs font-bold hover:bg-[#7dd8ed] transition-colors flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed"
                            >
                                <SaveIcon size={12} /> {saving ? 'Saving...' : 'Save'}
                            </button>
                        </div>
                    </div>
                    {testResult && (
                        <div className={`text-xs mt-1 ${testResult.success ? 'text-green-400' : 'text-red-400'}`}>
                            {testResult.message}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

/* ── Reusable sub-components ─────────────────────────────────────────────── */

function Field({ label, placeholder, value, onChange, hint, small }: {
    label: string;
    placeholder: string;
    value: string;
    onChange: (v: string) => void;
    hint?: string;
    small?: boolean;
}) {
    return (
        <div>
            <label className="block text-[10px] text-gray-500 uppercase tracking-wider mb-1.5 font-bold">{label}</label>
            <input
                type="text"
                value={value}
                onChange={e => onChange(e.target.value)}
                placeholder={placeholder}
                className={`w-full bg-[#151a1f] border border-[#3c5d80]/40 rounded-lg px-3 py-2 text-white placeholder-gray-600 focus:outline-none focus:border-[#5acce6] ${small ? 'text-[11px]' : 'text-xs'}`}
            />
            {hint && <p className="text-[9px] text-gray-600 mt-1">{hint}</p>}
        </div>
    );
}

function Toggle({ label, checked, onChange }: {
    label: string;
    checked: boolean;
    onChange: (v: boolean) => void;
}) {
    return (
        <label className="flex items-center gap-2 cursor-pointer group px-2 py-1.5 rounded hover:bg-[#151a1f] transition-colors">
            <div
                onClick={() => onChange(!checked)}
                className={`w-8 h-[18px] rounded-full relative transition-colors ${checked ? 'bg-[#5acce6]' : 'bg-[#3c5d80]/40'}`}
            >
                <div className={`absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white transition-all ${checked ? 'left-[16px]' : 'left-[2px]'}`} />
            </div>
            <span className="text-xs text-gray-300 group-hover:text-white transition-colors">{label}</span>
        </label>
    );
}
