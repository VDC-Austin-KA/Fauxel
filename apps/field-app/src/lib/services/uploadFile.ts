import { File, FileMode } from 'expo-file-system';
import { fetch as expoFetch } from 'expo/fetch';
import type { FileUploader } from '../queue/worker';
import { apiRoot, FauxelApi, Session } from '../api/fauxel';
import { uploadCapture } from '../upload/multipart';

/**
 * Resumable capture upload (M8.1): direct multipart parts of 16-64 MB, read from disk one part at a time, so a
 * multi-GB walk never enters JS memory whole and never hits Cloudflare's 100 MB body cap. The upload id/key are
 * checkpointed into the queue item; a retry sends only the parts the server is missing. See ../upload/multipart.ts.
 */
export function makeFileUploader(session: Session): FileUploader {
  const api = new FauxelApi(session);
  const root = apiRoot(session.baseUrl);
  return async (item, onProgress, checkpoint) => {
    const p = item.payload as {
      uri: string; name: string; mime: string; levelId?: string; capturedAt: string; cameraModel: string; uploadId?: string; uploadKey?: string;
    };
    const file = new File(p.uri);
    if (!file.exists) throw new Error('local file missing');
    const putPart = async (url: string, start: number, end: number, auth: boolean) => {
      const h = file.open(FileMode.ReadOnly);
      let bytes: Uint8Array<ArrayBuffer>;
      try {
        h.offset = start;
        bytes = await h.readBytes(end - start);
      } finally {
        h.close();
      }
      if (bytes.length !== end - start) throw new Error('local file changed during upload');
      // the API key only goes to the Fauxel API, never to a presigned storage URL
      const r = await expoFetch(url, { method: 'PUT', body: bytes, headers: auth ? api.headers() : {} });
      return { status: r.status, etag: r.headers.get('ETag') };
    };
    const cap = await uploadCapture(
      { projectId: item.projectId, name: p.name, size: file.size ?? 0, mime: p.mime, levelId: p.levelId, capturedAt: p.capturedAt,
        cameraModel: p.cameraModel, uploadId: p.uploadId, uploadKey: p.uploadKey },
      { api: (m, path, body) => (m === 'GET' ? api.get(path) : api.postJson(path, body)), putPart, checkpoint, apiRoot: root, onProgress },
    );
    return { status: 201, body: JSON.stringify(cap) };
  };
}

export const mimeFor = (name: string): string =>
  /\.mp4$/i.test(name) ? 'video/mp4' : /\.png$/i.test(name) ? 'image/png' : /\.jpe?g$/i.test(name) ? 'image/jpeg' : 'application/octet-stream';
