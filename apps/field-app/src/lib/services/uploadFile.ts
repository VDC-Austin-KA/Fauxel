import { File, UploadType } from 'expo-file-system';
import type { FileUploader } from '../queue/worker';
import { apiRoot, Session } from '../api/fauxel';

/**
 * Native multipart upload of one file to POST /projects/{pid}/captures (streamed from disk, with progress, so
 * multi-hundred-MB videos never enter JS memory). The v1 contract has no chunk/append endpoint, so "chunking" is
 * per file; each accepted POST becomes one capture. Cloudflare rejects bodies over 100 MB on Free/Pro plans:
 * oversized files need the direct/LAN URL (see README).
 */
export function makeFileUploader(session: Session): FileUploader {
  return async (item, onProgress) => {
    const p = item.payload as { uri: string; name: string; mime: string; levelId?: string; capturedAt: string; cameraModel: string };
    const file = new File(p.uri);
    if (!file.exists) throw new Error('local file missing');
    const parameters: Record<string, string> = { captured_at: p.capturedAt, camera_model: p.cameraModel, source: 'insta360' };
    if (p.levelId) parameters.level_id = p.levelId;
    const res = await file.upload(`${apiRoot(session.baseUrl)}/projects/${item.projectId}/captures`, {
      uploadType: UploadType.MULTIPART,
      fieldName: 'files[]',
      mimeType: p.mime,
      parameters,
      headers: { 'X-Fauxel-Key': session.apiKey, Accept: 'application/json' },
      sessionType: 'foreground',
      onProgress: ({ bytesSent, totalBytes }) => onProgress(bytesSent, totalBytes),
    });
    const ra = res.headers?.['retry-after'] ?? res.headers?.['Retry-After'];
    return { status: res.status, body: res.body, retryAfterMs: ra && /^\d+$/.test(ra) ? Number(ra) * 1000 : undefined };
  };
}

export const mimeFor = (name: string): string =>
  /\.mp4$/i.test(name) ? 'video/mp4' : /\.png$/i.test(name) ? 'image/png' : /\.jpe?g$/i.test(name) ? 'image/jpeg' : 'application/octet-stream';
