import { Job, api } from '../api';
import { FileText, Loader2 } from 'lucide-react';

interface Props {
  job: Job | null;
}

export function PdfViewer({ job }: Props) {
  if (!job) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-muted select-none">
        <FileText className="w-16 h-16 opacity-10" strokeWidth={1} />
        <div className="text-center">
          <p className="font-medium text-sm">No drawing selected</p>
          <p className="text-xs mt-1 opacity-70">Click "Preview" on a completed job to view the PDF</p>
        </div>
      </div>
    );
  }

  if (job.status === 'pending' || job.status === 'processing') {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-muted select-none">
        <Loader2 className="w-12 h-12 text-accent animate-spin opacity-60" />
        <div className="text-center">
          <p className="font-medium text-sm capitalize">{job.status}…</p>
          <p className="text-xs mt-1 opacity-70">Processing <strong>{job.filename}</strong></p>
        </div>
      </div>
    );
  }

  if (job.status === 'error') {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-red-500 px-8 select-none">
        <p className="font-semibold text-sm">Processing failed</p>
        <pre className="text-xs bg-red-50 border border-red-200 rounded-lg p-4 w-full max-w-xl overflow-auto max-h-60 text-red-700 whitespace-pre-wrap">
          {job.error ?? 'Unknown error'}
        </pre>
      </div>
    );
  }

  return (
    <iframe
      key={job.id}
      src={api.pdfUrl(job.id)}
      title={`Preview — ${job.filename}`}
      className="w-full h-full border-0 bg-white"
    />
  );
}
