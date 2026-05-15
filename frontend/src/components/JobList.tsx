import { Download, FileText, Trash2 } from 'lucide-react';
import { api, Job } from '../api';

interface Props {
  jobs: Job[];
  selectedId: string | null;
  onSelect: (job: Job) => void;
  onDelete: (id: string) => void;
}

export function JobList({ jobs, selectedId, onSelect, onDelete }: Props) {
  if (!jobs.length) return <div className="p-4 text-sm text-slate-500">No drawings yet.</div>;
  return (
    <div className="space-y-2">
      {jobs.map((job) => (
        <button
          key={job.id}
          onClick={() => onSelect(job)}
          className={`w-full rounded-xl border p-3 text-left transition ${selectedId === job.id ? 'border-slate-900 bg-slate-100' : 'border-slate-200 bg-white hover:bg-slate-50'}`}
        >
          <div className="flex items-start gap-2">
            <FileText className="mt-0.5 h-4 w-4 text-slate-600" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold text-slate-900">{job.filename}</div>
              <div className="mt-1 text-xs text-slate-500">{job.status}</div>
              {job.status === 'done' && (
                <div className="mt-2 flex gap-2">
                  <a href={api.pdfUrl(job.id)} target="_blank" className="inline-flex items-center gap-1 rounded bg-slate-900 px-2 py-1 text-xs text-white" onClick={(e) => e.stopPropagation()}>
                    <Download className="h-3 w-3" /> PDF
                  </a>
                  <a href={api.dxfUrl(job.id)} className="inline-flex items-center gap-1 rounded bg-slate-200 px-2 py-1 text-xs text-slate-800" onClick={(e) => e.stopPropagation()}>
                    <Download className="h-3 w-3" /> DXF
                  </a>
                </div>
              )}
              {job.error && <pre className="mt-2 max-h-24 overflow-auto text-[10px] text-red-700">{job.error}</pre>}
            </div>
            <span
              role="button"
              tabIndex={0}
              onClick={(e) => { e.stopPropagation(); onDelete(job.id); }}
              className="rounded p-1 text-slate-400 hover:bg-red-50 hover:text-red-600"
            >
              <Trash2 className="h-4 w-4" />
            </span>
          </div>
        </button>
      ))}
    </div>
  );
}
