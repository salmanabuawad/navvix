import { useState, useEffect, useCallback, useRef } from 'react';
import { Layers, RefreshCw, AlertCircle, BookOpen, Cpu } from 'lucide-react';
import { api, Job } from './api';
import { UploadZone }    from './components/UploadZone';
import { JobList }       from './components/JobList';
import { PdfViewer }     from './components/PdfViewer';
import { TrainingPanel } from './components/TrainingPanel';

type Mode = 'process' | 'train';

export default function App() {
  const [mode,       setMode]       = useState<Mode>('process');
  const [jobs,       setJobs]       = useState<Job[]>([]);
  const [selected,   setSelected]   = useState<Job | null>(null);
  const [uploading,  setUploading]  = useState(false);
  const [uploadErr,  setUploadErr]  = useState<string | null>(null);
  const [fetchErr,   setFetchErr]   = useState<string | null>(null);

  const selectedIdRef = useRef<string | null>(null);
  selectedIdRef.current = selected?.id ?? null;

  // ── Fetch / smart poll ───────────────────────────────────────────────────
  const fetchJobs = useCallback(async () => {
    try {
      const list = await api.listJobs();
      setJobs(list);
      setFetchErr(null);
      if (selectedIdRef.current) {
        const fresh = list.find(j => j.id === selectedIdRef.current);
        if (fresh) setSelected(fresh);
      }
    } catch (e) {
      setFetchErr(e instanceof Error ? e.message : 'Failed to fetch jobs');
    }
  }, []);

  useEffect(() => { fetchJobs(); }, []); // eslint-disable-line

  useEffect(() => {
    const active = jobs.some(j => j.status === 'pending' || j.status === 'processing');
    if (!active) return;
    const t = setTimeout(fetchJobs, 2000);
    return () => clearTimeout(t);
  }, [jobs, fetchJobs]);

  // ── Upload ───────────────────────────────────────────────────────────────
  async function handleUpload(file: File) {
    setUploading(true); setUploadErr(null);
    try {
      const job = await api.upload(file);
      setJobs(prev => [job, ...prev]);
      setSelected(job);
      setMode('process');
    } catch (e) {
      setUploadErr(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  }

  // ── Delete ───────────────────────────────────────────────────────────────
  async function handleDelete(id: string) {
    try {
      await api.deleteJob(id);
      setJobs(prev => prev.filter(j => j.id !== id));
      if (selected?.id === id) setSelected(null);
    } catch (e) { alert(e instanceof Error ? e.message : 'Delete failed'); }
  }

  // ── Render ───────────────────────────────────────────────────────────────
  return (
    <div className="h-screen flex flex-col overflow-hidden bg-bg">

      {/* ── Header ── */}
      <header className="shrink-0 h-12 bg-header flex items-center justify-between px-5 shadow-md z-10">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg bg-white/20 flex items-center justify-center">
            <Layers className="w-4 h-4 text-white" />
          </div>
          <span className="font-bold text-white tracking-wide text-sm">navvix</span>
          <span className="text-white/40 text-xs font-normal hidden sm:inline">DXF Dimensioner</span>
        </div>

        {/* Mode tabs */}
        <div className="flex items-center gap-1 bg-white/10 rounded-lg p-0.5">
          {([
            { id: 'process' as Mode, label: 'Process', Icon: Cpu },
            { id: 'train'   as Mode, label: 'Training', Icon: BookOpen },
          ] as const).map(({ id, label, Icon }) => (
            <button
              key={id}
              onClick={() => setMode(id)}
              className={[
                'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold transition-colors',
                mode === id ? 'bg-white text-header' : 'text-white/80 hover:text-white hover:bg-white/10',
              ].join(' ')}
            >
              <Icon className="w-3.5 h-3.5" />{label}
            </button>
          ))}
        </div>

        <button onClick={fetchJobs} title="Refresh"
          className="p-2 rounded hover:bg-white/10 text-white/70 hover:text-white transition-colors">
          <RefreshCw className="w-4 h-4" />
        </button>
      </header>

      {/* ── Body ── */}
      <div className="flex-1 flex min-h-0">

        {/* ══════════════════════════ PROCESS MODE ══════════════════════════ */}
        {mode === 'process' && (
          <>
            {/* Left: upload + list */}
            <aside className="w-80 xl:w-96 shrink-0 flex flex-col border-r border-border bg-bg">
              <div className="p-4 border-b border-border shrink-0">
                <UploadZone onUpload={handleUpload} disabled={uploading} />
                {uploadErr && (
                  <div className="mt-2 flex items-start gap-1.5 text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg p-2">
                    <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />{uploadErr}
                  </div>
                )}
              </div>
              <div className="px-4 pt-3 pb-2 flex items-center justify-between shrink-0">
                <h2 className="text-xs font-semibold text-muted uppercase tracking-wider">Drawings</h2>
                <span className="text-xs text-muted bg-white border border-border rounded-full px-2 py-0.5">
                  {jobs.length}
                </span>
              </div>
              {fetchErr && (
                <div className="mx-4 mb-2 flex items-start gap-1.5 text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg p-2">
                  <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />{fetchErr}
                </div>
              )}
              <div className="flex-1 overflow-y-auto scrollbar-thin px-4 pb-4">
                <JobList
                  jobs={jobs}
                  selectedId={selected?.id ?? null}
                  onSelect={job => setSelected(job)}
                  onDelete={handleDelete}
                />
              </div>
            </aside>

            {/* Right: PDF preview */}
            <main className="flex-1 min-w-0 bg-white flex flex-col">
              {selected && (
                <div className="shrink-0 px-5 py-2.5 border-b border-border bg-bg flex items-center gap-2 flex-wrap">
                  <span className="text-xs font-medium text-muted">Preview:</span>
                  <span className="text-xs font-semibold text-text truncate">{selected.filename}</span>
                  {selected.model_used && (
                    <span className={`ml-auto text-[10px] px-2 py-0.5 rounded-full font-medium ${
                      selected.model_used === 'v13_learned'
                        ? 'bg-green-100 text-green-700'
                        : 'bg-slate-100 text-slate-600'
                    }`}>
                      {selected.model_used === 'v13_learned' ? '⚡ Learned model' : '📐 Rule-based'}
                    </span>
                  )}
                </div>
              )}
              <div className="flex-1 min-h-0">
                <PdfViewer job={selected} />
              </div>
            </main>
          </>
        )}

        {/* ══════════════════════════ TRAIN MODE ═══════════════════════════ */}
        {mode === 'train' && (
          <div className="flex-1 flex min-h-0">
            <div className="w-full max-w-2xl mx-auto flex flex-col min-h-0">
              <TrainingPanel />
            </div>
          </div>
        )}

      </div>
    </div>
  );
}
