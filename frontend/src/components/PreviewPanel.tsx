import { api, Job } from '../api';

export function PreviewPanel({ job }: { job: Job | null }) {
  if (!job) {
    return <div className="flex h-full items-center justify-center text-sm text-slate-500">Select a processed drawing.</div>;
  }
  if (job.status !== 'done') {
    return <div className="flex h-full items-center justify-center text-sm text-slate-500">Status: {job.status}</div>;
  }
  return (
    <div className="h-full w-full bg-slate-100 p-4">
      <iframe title="preview" src={api.pdfUrl(job.id)} className="h-full w-full rounded-xl border bg-white" />
    </div>
  );
}
