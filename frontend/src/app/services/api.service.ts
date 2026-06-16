import { inject, Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  JobsResponse, Settings, StatusResponse, VideoDetail,
  VideosResponse, VideoSummary, User, KeyOwner, MediaType, SortField, SortOrder,
} from '../models/api.models';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  status(): Observable<StatusResponse> {
    return this.http.get<StatusResponse>('/api/status');
  }

  assets(params: {
    page: number; per_page: number; sort: SortField; order: SortOrder;
    media: MediaType; min_mb: number; codec?: string; user?: string; search?: string;
    keys?: number[];
  }): Observable<VideosResponse> {
    const p: Record<string, string> = {
      page: String(params.page), per_page: String(params.per_page),
      sort: params.sort, order: params.order, media: params.media,
      min_mb: String(params.min_mb),
    };
    if (params.codec) p['codec'] = params.codec;
    if (params.user) p['user'] = params.user;
    if (params.search) p['search'] = params.search;
    if (params.keys && params.keys.length) p['keys'] = params.keys.join(',');
    return this.http.get<VideosResponse>('/api/assets', { params: p });
  }

  keyOwners(): Observable<{ owners: KeyOwner[] }> {
    return this.http.get<{ owners: KeyOwner[] }>('/api/keys');
  }

  assetDetail(id: string): Observable<VideoDetail> {
    return this.http.get<VideoDetail>(`/api/asset/${encodeURIComponent(id)}`);
  }

  users(): Observable<{ users: User[] }> {
    return this.http.get<{ users: User[] }>('/api/users');
  }

  jobs(): Observable<JobsResponse> {
    return this.http.get<JobsResponse>('/api/jobs');
  }

  enqueue(ids: string[], names: Record<string, string>, sizes: Record<string, number>, settings: Settings, medias: Record<string, string> = {}, downloadOnly = false): Observable<{ added: string[] }> {
    return this.http.post<{ added: string[] }>('/api/enqueue', {
      ...settings, ids, names, sizes, medias, download_only: downloadOnly,
    });
  }

  resolve(items: string[]): Observable<{ assets: VideoSummary[]; errors: { input: string; reason: string }[] }> {
    return this.http.post<{ assets: VideoSummary[]; errors: { input: string; reason: string }[] }>(
      '/api/resolve', { items });
  }

  cancel(id: string): Observable<{ ok: boolean }> {
    return this.http.post<{ ok: boolean }>(`/api/cancel/${encodeURIComponent(id)}`, {});
  }

  confirm(id: string): Observable<{ ok: boolean }> {
    return this.http.post<{ ok: boolean }>(`/api/confirm/${encodeURIComponent(id)}`, {});
  }

  discard(id: string): Observable<{ ok: boolean }> {
    return this.http.post<{ ok: boolean }>(`/api/discard/${encodeURIComponent(id)}`, {});
  }

  clearDone(): Observable<{ removed: string[] }> {
    return this.http.post<{ removed: string[] }>('/api/clear', {});
  }

  thumbnailUrl(id: string, size = 'thumbnail'): string {
    return `/api/thumbnail/${encodeURIComponent(id)}?size=${size}`;
  }

  downloadUrl(id: string, name?: string): string {
    return `/api/download/${encodeURIComponent(id)}${name ? '?name=' + encodeURIComponent(name) : ''}`;
  }

  previewUrl(id: string): string {
    return `/api/preview/${encodeURIComponent(id)}`;
  }
}
