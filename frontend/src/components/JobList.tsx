import { Job, api } from '../api';
import {
  CheckCircle2, XCircle, Loader2, Clock,
  Eye, Download, Trash2, FileWarning,
} from 'lucide-react';

interface Props {
  jobs:       Job[];
  selectedId: string | null;
  onSelect:   (job: Job) => void;
  onDelete:   (id: string) => void;
}

/* ── Status badge ────────────────────────────────────────────────────────── */
function StatusBadge({ status }: { status: Job['status'] }) {
  const cfg = {
    pending:    { icon: <Clock     className="w-3.5 h-3.5" />, label: 'Pending',    cls: 'bg-slate-100  text-slate-600'  },
    processing: { icon: <Loader2   className="w-3.5 h-3.5 animate-spin" />, label: 'Processing', cls: 'bg-amber-100 text-amber-700'  },
    done:       { icon: <CheckCircle2 className="w-3.5 h-3.5" />, label: 'Done',    cls: 'bg-green-100  text-green-700'  },
    error:      { icon: <XCircle   className="w-3.5 h-3.5" />, label: 'Error',      cls: 'bg-red-100    text-red-700'    },
  }[status];

  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cfg.cls}`}>
      {cfg.icon}{cfg.label}
    </span>
  );
}

/* ── Helpers ─────────────────────────────────────────────────────────────── */
function fmtSize(bytes: number): string {
  if (bytes < 1024)       return `${bytes} B`;
  if (bytes < 1024**2)    return `${(bytes/1024).toFixed(1)} KB`;
  return `${(bytes/1024**2).toFixed(1)} MB`;
}

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
}

/* ── Single job row ──────────────────────────────────────────────────────── */
function JobRow({ job, selected, onSelect, onDelete }: {
  job: Job; selected: boolean;
  onSelect: () => void; onDelete: () => void;
}) {
  const done = job.status === 'done';

  return (
    <div
      className={[
        'group rounded-xl border px-4 py-3 transition-all cursor-pointer',
        selected
          ? 'border-accent bg-blue-50 shadow-sm'
          : 'border-border bg-white hover:border-accent/50 hover:shadow-sm',
      ].join(' ')}
      onClick={onSelect}
    >
      {/* Top row */}
      <div className="flex items-start justify-between gap-2 min-w-0">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-text truncate" title={job.filename}>
            {job.filename}
          </p>
          <p className="text-xs text-muted mt-0.5">
            {fmtSize(job.size_bytes)} · {fmtDate(job.created_at)}
          </p>
        </div>
        <StatusBadge status={job.status} />
      </div>

      {/* Stats (only when done) */}
      {done && job.dims_total != null && (
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted">
          <span>X axes: <strong className="text-text">{job.x_axes}</strong></span>
          <span>Y axes: <strong className="text-text">{job.y_axes}</strong></span>
          <span>Dims: <strong className="text-text">{job.dims_total}</strong></span>
          <span>Internal: <strong className="text-text">{job.dims_internal}</strong></span>
        </div>
      )}

      {/* Error snippet */}
      {job.status === 'error' && job.error && (
        <div className="mt-2 flex items-start gap-1.5 text-xs text-red-600 bg-red-50 rounded-lg p-2">
          <FileWarning className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span className="line-clamp-2 font-mono">{job.error.split('\n').pop()}</span>
        </div>
      )}

      {/* Actions */}
      <div className="mt-3 flex items-center gap-2" onClick={e => e.stopPropagation()}>
        <button
          disabled={!done}
          onClick={onSelect}
          title="Preview PDF"
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors
                     disabled:opacity-40 disabled:cursor-not-allowed
                     bg-accent text-white hover:bg-accent-hover disabled:bg-slate-200 disabled:text-slate-400"
        >
          <Eye className="w-3.5 h-3.5" /> Preview
        </button>

        <a
          href={done ? api.dxfUrl(job.id) : undefined}
          download
          onClick={e => !done && e.preventDefault()}
          title="Download dimensioned DXF"
          className={[
            'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors border',
            done
              ? 'border-border text-text hover:bg-gray-50'
              : 'border-transparent text-slate-400 cursor-not-allowed pointer-events-none',
          ].join(' ')}
        >
          <Download className="w-3.5 h-3.5" /> DXF
        </a>

        <button
          onClick={onDelete}
          title="Delete job"
          className="ml-auto p-1.5 rounded-lg text-muted hover:text-red-600 hover:bg-red-50 transition-colors opacity-0 group-hover:opacity-100"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
}

/* ── Job list ─────────────────────────────────────────────────────────────── */
export function JobList({ jobs, selectedId, onSelect, onDelete }: Props) {
  if (jobs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-40 text-center text-muted text-sm gap-2">
        <p className="text-3xl opacity-20">📐</p>
        <p>No drawings yet.</p>
        <p className="text-xs">Upload a DXF to get started.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {jobs.map(job => (
        <JobRow
          key={job.id}
          job={job}
          selected={job.id === selectedId}
          onSelect={() => onSelect(job)}
          onDelete={() => onDelete(job.id)}
        />
      ))}
    </div>
  );
}
