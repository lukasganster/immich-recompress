export type JobStatus =
  | 'idle' | 'queued' | 'downloading' | 'encoding' | 'replacing'
  | 'review' | 'done' | 'downloaded' | 'encoded' | 'skipped'
  | 'error' | 'cancelled' | 'discarded' | 'processed';

export type MediaType = 'video' | 'image' | 'motionphoto';
export type SortField = 'size' | 'savings' | 'date' | 'name' | 'duration';
export type SortOrder = 'asc' | 'desc';

export interface VideoSummary {
  id: string;
  media: MediaType;
  name: string;
  size: number;
  size_human: string;
  potential: number;
  duration: number;
  duration_human: string;
  codec: string;
  resolution: string;
  bitrate: number | null;
  date: string | null;
  owner_id: string;
  owner_name: string;
  is_favorite: boolean;
  is_archived: boolean;
  albums: { id: string; name: string }[];
  people: string[];
  status: JobStatus;
}

export interface VideoDetail {
  id: string;
  name: string;
  original_path: string;
  mime_type: string;
  checksum: string;
  size: number;
  size_human: string;
  duration: number;
  duration_human: string;
  exif: {
    codec: string;
    resolution: string;
    width: number | null;
    height: number | null;
    bitrate: number | null;
    fps: number | null;
    make: string | null;
    model: string | null;
    lens: string | null;
    lat: number | null;
    lon: number | null;
    city: string | null;
    country: string | null;
    orientation: string | null;
  };
  owner: { id: string; name: string; email: string };
  albums: { id: string; name: string }[];
  people: { id: string; name: string }[];
  tags: string[];
  is_favorite: boolean;
  is_archived: boolean;
  is_trashed: boolean;
  created_at: string | null;
  updated_at: string | null;
  job_status: { status: JobStatus; progress: number; log: string };
}

export interface JobPublic {
  id: string;
  name: string;
  size: number | null;
  status: JobStatus;
  progress: number;
  log: string;
  codec: string | null;
  new_codec: string | null;
  new_id: string | null;
  old_size: number | null;
  new_size: number | null;
  savings: number | null;
  savings_human: string | null;
  confirm: boolean;
  media: MediaType;
  encoder: string;
  quality: number;
  threads: number | null;
  photo_target_savings: number;
  compress_raw: boolean;
  preset: string;
  resolution: string;
  motion_action: string;
  skip_codecs: string;
  min_savings: number;
  replace: boolean;
  download_only: boolean;
  backup_dir: string;
  started_at: string | null;
}

export interface JobsResponse {
  active: string | null;
  queue: string[];
  stats: {
    queued: number;
    active: number;
    review: number;
    done: number;
    skipped: number;
    error: number;
    saved_bytes: number;
  };
  jobs: Record<string, JobPublic>;
}

export interface VideosResponse {
  total: number;
  total_size: number;
  total_potential: number;
  page: number;
  per_page: number;
  assets: VideoSummary[];
  error?: string;
}

export interface StatusResponse {
  ok: boolean;
  msg: string;
  env: { IMMICH_URL: string; api_key_set: boolean };
  handbrake: boolean;
  ffprobe: boolean;
  ffmpeg: boolean;
  immich_version?: string;
}

export interface User {
  id: string;
  name: string;
  email: string;
}

export interface KeyOwner {
  key_idx: number;
  id: string;
  name: string;
  email: string;
}

export interface Settings {
  media: MediaType;
  encoder: string;
  quality: number;
  threads: number;
  photo_target_savings: number;
  compress_raw: boolean;
  preset: string;
  resolution: string;
  motion_action: string;
  skip_codecs: string;
  min_savings: number;
  replace: boolean;
  confirm: boolean;
}

/** One encoder the running HandBrake build supports (from /api/capabilities). */
export interface EncoderInfo {
  id: string;
  label: string;
  hw: boolean;
  qmin: number;
  qmax: number;
  qdefault: number;
  /** Quality direction: 'low' = lower is better (RF/CRF), 'high' = higher is better (VideoToolbox). */
  qbetter: 'low' | 'high';
  /** Whether a CPU-core count can be set for this (software) encoder. */
  cores: boolean;
}

export interface Capabilities {
  encoders: EncoderInfo[];
  cpu_count: number;
}

export interface ProcessedEntry {
  status: 'done';
  old_size: number | null;
  new_size: number | null;
  savings: number | null;
  media: MediaType;
  ts: number;
}

export interface SseJobUpdate {
  id: string;
  status: JobStatus;
  progress: number;
  log: string;
  new_size: number | null;
  savings: number | null;
}
