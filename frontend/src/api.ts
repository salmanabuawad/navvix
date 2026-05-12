// ── Job types ────────────────────────────────────────────────────────────────
export type JobStatus = "pending" | "processing" | "done" | "error";

export interface Job {
  id:            string;
  filename:      string;
  size_bytes:    number;
  status:        JobStatus;
  created_at:    string;
  started_at?:   string;
  done_at?:      string;
  x_axes?:       number;
  y_axes?:       number;
  dims_total?:   number;
  dims_internal?:number;
  model_used?:   string;
  error?:        string;
}

// ── Training types ────────────────────────────────────────────────────────────
export interface TrainingFile {
  name:        string;
  size_bytes:  number;
  uploaded_at: string;
}

export interface TrainingStatus {
  running:     boolean;
  started_at:  string | null;
  finished_at: string | null;
  error:       string | null;
  last_report: LearnReport | null;
}

export interface LearnReport {
  files_processed: number;
  files_failed:    number;
  failed_files:    string[];
  learned_patterns: {
    chain_offset_ratio:   number;
    overall_offset_ratio: number;
    min_len_ratio:        number;
    avg_internal_dims:    number;
    style: Record<string, number>;
  };
  per_file: { file: string; dims: number; base_dim: number; int_dims: number }[];
}

export interface StyleModel {
  version:    string;
  trained_on: number;
  external: {
    chain_offset_ratio:   number;
    overall_offset_ratio: number;
    min_len_ratio:        number;
  };
  internal: {
    max_dims: number;
  };
  style: Record<string, number>;
}

// ── HTTP helper ───────────────────────────────────────────────────────────────
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init);
  if (!res.ok) {
    const msg = await res.text().catch(() => res.statusText);
    throw new Error(msg || `HTTP ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── API client ────────────────────────────────────────────────────────────────
export const api = {
  // Jobs
  upload(file: File): Promise<Job> {
    const fd = new FormData(); fd.append("file", file);
    return request<Job>("/jobs", { method: "POST", body: fd });
  },
  listJobs():           Promise<Job[]>  { return request("/jobs"); },
  getJob(id: string):   Promise<Job>    { return request(`/jobs/${id}`); },
  deleteJob(id: string):Promise<void>   { return request(`/jobs/${id}`, { method: "DELETE" }); },
  pdfUrl(id: string):   string          { return `/api/jobs/${id}/pdf`; },
  dxfUrl(id: string):   string          { return `/api/jobs/${id}/dxf`; },

  // Training
  listTrainingFiles():                     Promise<TrainingFile[]>  { return request("/training/files"); },
  uploadTrainingFile(file: File):          Promise<{name:string}>   {
    const fd = new FormData(); fd.append("file", file);
    return request("/training/files", { method: "POST", body: fd });
  },
  deleteTrainingFile(name: string):        Promise<void>  { return request(`/training/files/${encodeURIComponent(name)}`, { method: "DELETE" }); },
  startTraining():                         Promise<{status:string;files:number}> { return request("/training/train", { method: "POST" }); },
  getTrainingStatus():                     Promise<TrainingStatus>  { return request("/training/status"); },
  getModel():                              Promise<StyleModel|null> { return request("/training/model"); },
  resetModel():                            Promise<void>  { return request("/training/model", { method: "DELETE" }); },
};
