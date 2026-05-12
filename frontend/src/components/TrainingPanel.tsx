import { useState, useEffect, useCallback, useRef } from 'react';
import {
  UploadCloud, Trash2, Play, CheckCircle2, XCircle,
  Loader2, BookOpen, AlertCircle, RefreshCw, RotateCcw,
} from 'lucide-react';
import { api, TrainingFile, TrainingStatus, StyleModel } from '../api';

function fmtSize(b: number) {
  if (b < 1024) return `${b} B`;
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 ** 2).toFixed(1)} MB`;
}

function StatPill({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex flex-col items-center bg-white border border-border rounded-xl px-4 py-3 min-w-[90px]">
      <span className="text-lg font-bold text-accent">{value}</span>
      <span className="text-[10px] text-muted uppercase tracking-wide mt-0.5">{label}</span>
    </div>
  );
}

export function TrainingPanel() {
  const [files,     setFiles]     = useState<TrainingFile[]>([]);
  const [status,    setStatus]    = useState<TrainingStatus | null>(null);
  const [model,     setModel]     = useState<StyleModel | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error,     setError]     = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const [f, s, m] = await Promise.all([
        api.listTrainingFiles(),
        api.getTrainingStatus(),
        api.getModel(),
      ]);
      setFiles(f); setStatus(s); setModel(m); setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // Poll while training runs
  useEffect(() => {
    if (!status?.running) return;
    const t = setTimeout(refresh, 1500);
    return () => clearTimeout(t);
  }, [status, refresh]);

  async function handleUpload(fileList: FileList | null) {
    if (!fileList) return;
    setUploading(true); setError(null);
    try {
      await Promise.all(Array.from(fileList).map(f => api.uploadTrainingFile(f)));
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  }

  async function handleDelete(name: string) {
    try { await api.deleteTrainingFile(name); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Delete failed'); }
  }

  async function handleTrain() {
    setError(null);
    try { await api.startTraining(); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Train failed'); }
  }

  async function handleReset() {
    if (!confirm('Reset the learned model? Future jobs will use rule-based processing.')) return;
    try { await api.resetModel(); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Reset failed'); }
  }

  const isRunning = status?.running ?? false;
  const hasModel  = model != null;

  return (
    <div className="flex-1 overflow-y-auto scrollbar-thin p-6 space-y-6">

      {/* ── Header ── */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-bold text-text flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-accent" /> Style Learning
          </h2>
          <p className="text-xs text-muted mt-0.5">
            Upload dimensioned DXF samples → train → apply to new drawings
          </p>
        </div>
        <button onClick={refresh} className="p-2 rounded hover:bg-gray-100 text-muted transition-colors" title="Refresh">
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      {error && (
        <div className="flex items-start gap-2 text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />{error}
        </div>
      )}

      {/* ── Upload zone ── */}
      <div
        onClick={() => !uploading && inputRef.current?.click()}
        className={[
          'flex flex-col items-center gap-2 rounded-xl border-2 border-dashed px-6 py-7 cursor-pointer transition-colors',
          uploading
            ? 'border-gray-200 bg-gray-50 cursor-not-allowed opacity-60'
            : 'border-border bg-white hover:border-accent hover:bg-blue-50/30',
        ].join(' ')}
      >
        {uploading
          ? <Loader2 className="w-8 h-8 text-accent animate-spin" />
          : <UploadCloud className="w-8 h-8 text-accent" strokeWidth={1.5} />}
        <div className="text-center">
          <p className="text-sm font-semibold text-text">
            {uploading ? 'Uploading…' : 'Drop training DXFs here'}
          </p>
          <p className="text-xs text-muted mt-0.5">
            Must already contain dimensions · .dxf / .dwfx · multiple files OK
          </p>
        </div>
        <input
          ref={inputRef} type="file" multiple accept=".dxf,.dwfx" className="hidden"
          onChange={e => handleUpload(e.target.files)} disabled={uploading}
        />
      </div>

      {/* ── File list ── */}
      {files.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs font-semibold text-muted uppercase tracking-wider">
              Training files ({files.length})
            </p>
          </div>
          <div className="space-y-1.5">
            {files.map(f => (
              <div key={f.name}
                className="group flex items-center gap-3 bg-white border border-border rounded-lg px-3 py-2">
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium text-text truncate">{f.name}</p>
                  <p className="text-[10px] text-muted">{fmtSize(f.size_bytes)}</p>
                </div>
                <button
                  onClick={() => handleDelete(f.name)}
                  className="p-1 rounded opacity-0 group-hover:opacity-100 text-muted hover:text-red-600 hover:bg-red-50 transition-all"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Train button ── */}
      <button
        onClick={handleTrain}
        disabled={isRunning || files.length === 0}
        className="w-full flex items-center justify-center gap-2 py-3 rounded-xl font-semibold text-sm transition-all
                   bg-accent text-white hover:bg-accent-hover
                   disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {isRunning
          ? <><Loader2 className="w-4 h-4 animate-spin" /> Training…</>
          : <><Play className="w-4 h-4" /> Train Model ({files.length} file{files.length !== 1 ? 's' : ''})</>}
      </button>

      {/* ── Training status ── */}
      {status?.last_report && (
        <div className="bg-white border border-border rounded-xl p-4 space-y-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-green-700">
            <CheckCircle2 className="w-4 h-4" /> Last training result
          </div>
          <div className="flex flex-wrap gap-2">
            <StatPill label="Files"    value={status.last_report.files_processed} />
            <StatPill label="Failed"   value={status.last_report.files_failed} />
            <StatPill label="Avg dims" value={status.last_report.learned_patterns.avg_internal_dims.toFixed(1)} />
            <StatPill label="Chain off" value={status.last_report.learned_patterns.chain_offset_ratio.toFixed(3)} />
            <StatPill label="Overall off" value={status.last_report.learned_patterns.overall_offset_ratio.toFixed(3)} />
          </div>
        </div>
      )}

      {status?.error && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-4">
          <p className="text-xs font-semibold text-red-700 flex items-center gap-1.5 mb-2">
            <XCircle className="w-3.5 h-3.5" /> Training failed
          </p>
          <pre className="text-[10px] text-red-600 whitespace-pre-wrap font-mono line-clamp-6">
            {status.error}
          </pre>
        </div>
      )}

      {/* ── Current model ── */}
      {hasModel && (
        <div className="bg-green-50 border border-green-200 rounded-xl p-4 space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold text-green-800 flex items-center gap-1.5">
              <CheckCircle2 className="w-4 h-4" />
              Learned model active — new jobs use this style
            </p>
            <button
              onClick={handleReset}
              className="flex items-center gap-1 text-xs text-muted hover:text-red-600 transition-colors"
              title="Reset to rule-based"
            >
              <RotateCcw className="w-3 h-3" /> Reset
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            <StatPill label="Trained on"  value={model!.trained_on} />
            <StatPill label="Chain ratio" value={model!.external.chain_offset_ratio.toFixed(3)} />
            <StatPill label="Overall ratio" value={model!.external.overall_offset_ratio.toFixed(3)} />
            <StatPill label="Max int dims" value={model!.internal.max_dims} />
          </div>
        </div>
      )}

      {!hasModel && !isRunning && (
        <div className="text-center text-xs text-muted py-4">
          No model trained yet — jobs use <strong>v12 rule-based</strong> processing
        </div>
      )}

    </div>
  );
}
