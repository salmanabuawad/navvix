export type JobStatus = 'pending' | 'processing' | 'done' | 'error';

export interface Job {
  id: string;
  filename: string;
  status: JobStatus;
  size_bytes: number;
  error?: string | null;
  report?: Record<string, unknown> | null;
  created_at: string;
  started_at?: string | null;
  done_at?: string | null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init);
  if (!res.ok) {
    const msg = await res.text().catch(() => res.statusText);
    throw new Error(msg || `HTTP ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  upload(file: File): Promise<Job> {
    const fd = new FormData();
    fd.append('file', file);
    return request<Job>('/jobs', { method: 'POST', body: fd });
  },
  listJobs(): Promise<Job[]> {
    return request<Job[]>('/jobs');
  },
  deleteJob(id: string): Promise<void> {
    return request<void>(`/jobs/${id}`, { method: 'DELETE' });
  },
  pdfUrl(id: string): string {
    return `/api/jobs/${id}/pdf`;
  },
  pngUrl(id: string): string {
    return `/api/jobs/${id}/png`;
  },
  dxfUrl(id: string): string {
    return `/api/jobs/${id}/dxf`;
  },
};
