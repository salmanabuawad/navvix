import { useCallback, useEffect, useState } from 'react';
import { Layers, RefreshCw } from 'lucide-react';
import { api, Job } from './api';
import { UploadZone } from './components/UploadZone';
import { JobList } from './components/JobList';
import { PreviewPanel } from './components/PreviewPanel';

export default function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchJobs = useCallback(async () => {
    try {
      const data = await api.listJobs();
      setJobs(data);
      setSelected((old) => old ? data.find((j) => j.id === old.id) ?? null : data[0] ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load jobs');
    }
  }, []);

  useEffect(() => { fetchJobs(); }, [fetchJobs]);
  useEffect(() => {
    if (!jobs.some((j) => j.status === 'pending' || j.status === 'processing')) return;
    const id = window.setTimeout(fetchJobs, 2000);
    return () => window.clearTimeout(id);
  }, [jobs, fetchJobs]);

  async function upload(file: File) {
    setBusy(true);
    setError(null);
    try {
      const job = await api.upload(file);
      setJobs((prev) => [job, ...prev]);
      setSelected(job);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    await api.deleteJob(id);
    setJobs((prev) => prev.filter((j) => j.id !== id));
    if (selected?.id === id) setSelected(null);
  }

  return (
    <div className="flex h-screen flex-col bg-slate-50 text-slate-900">
      <header className="flex h-14 items-center justify-between bg-slate-950 px-5 text-white">
        <div className="flex items-center gap-2">
          <div className="rounded-lg bg-white/15 p-1.5"><Layers className="h-4 w-4" /></div>
          <div>
            <div className="text-sm font-bold tracking-wide">Navvix</div>
            <div className="text-[11px] text-white/50">Semantic DXF dimensioning</div>
          </div>
        </div>
        <button onClick={fetchJobs} className="rounded-lg p-2 text-white/70 hover:bg-white/10 hover:text-white"><RefreshCw className="h-4 w-4" /></button>
      </header>
      <div className="flex min-h-0 flex-1">
        <aside className="flex w-96 flex-col border-r bg-white">
          <div className="border-b p-4"><UploadZone onUpload={upload} disabled={busy} />{error && <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-2 text-xs text-red-700">{error}</div>}</div>
          <div className="flex items-center justify-between px-4 py-3"><span className="text-xs font-semibold uppercase tracking-wider text-slate-500">Drawings</span><span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs">{jobs.length}</span></div>
          <div className="min-h-0 flex-1 overflow-auto px-4 pb-4"><JobList jobs={jobs} selectedId={selected?.id ?? null} onSelect={setSelected} onDelete={remove} /></div>
        </aside>
        <main className="min-w-0 flex-1"><PreviewPanel job={selected} /></main>
      </div>
    </div>
  );
}
