import { Directory, File, FileMode, Paths } from 'expo-file-system';
import { OscClient, OscFileEntry } from '../osc/client';
import { DownloadError, rangeDownload } from '../download/rangeDownload';
import { sha256File } from '../download/sha256';
import { getDb } from '../queue/db';

export type DownloadResult = { uri: string; sha256: string; size: number; name: string };

function dir(): Directory {
  const d = new Directory(Paths.document, 'camera');
  if (!d.exists) d.create({ intermediates: true, idempotent: true });
  return d;
}

const safeName = (n: string) => n.replace(/[^A-Za-z0-9._-]/g, '_');

/**
 * Download one camera file to app storage over the camera Wi-Fi. Chunked HTTP Range into <name>.part (resumes from
 * the bytes on disk), then SHA-256 over the finished file and rename. Falls back to a single full GET when the
 * camera ignores Range.
 */
export async function downloadCameraFile(
  osc: OscClient,
  entry: OscFileEntry,
  opts: { walkId?: string; onProgress?: (done: number, total: number | null) => void; shouldStop?: () => boolean } = {},
): Promise<DownloadResult> {
  const name = safeName(entry.name);
  const url = osc.resolveFileUrl(entry.fileUrl);
  const finalFile = new File(dir(), name);
  const partFile = new File(dir(), `${name}.part`);
  if (!partFile.exists) partFile.create();

  const db = await getDb();
  await db.runAsync(
    `INSERT INTO downloads(url,name,size,walk_id,status) VALUES(?,?,?,?,'downloading')
     ON CONFLICT(url) DO UPDATE SET status='downloading', walk_id=COALESCE(excluded.walk_id, downloads.walk_id)`,
    url, name, entry.size ?? null, opts.walkId ?? null);

  const handle = () => partFile.open(FileMode.ReadWrite);
  try {
    const size = await rangeDownload({
      url,
      expectedSize: entry.size,
      onProgress: opts.onProgress,
      shouldStop: opts.shouldStop,
      part: {
        size: () => partFile.size ?? 0,
        write: async (offset, bytes) => {
          const h = handle();
          try {
            h.offset = offset;
            await h.writeBytes(bytes);
          } finally {
            h.close();
          }
        },
        truncate: () => {
          partFile.delete();
          partFile.create();
        },
      },
      fetchRange: async (u, range) => {
        const r = await fetch(u, { headers: { Range: range } });
        return { status: r.status, headers: r.headers, bytes: async () => new Uint8Array(await r.arrayBuffer()) };
      },
    }).catch(async (e) => {
      if (e instanceof DownloadError && e.message === 'rangeUnsupported') {
        await File.downloadFileAsync(url, partFile, { idempotent: true });
        return partFile.size ?? 0;
      }
      throw e;
    });
    const sha256 = await sha256File(partFile);
    if (finalFile.exists) finalFile.delete();
    await partFile.move(finalFile);
    await db.runAsync(`UPDATE downloads SET status='done', local_uri=?, sha256=?, size=? WHERE url=?`, finalFile.uri, sha256, size, url);
    return { uri: finalFile.uri, sha256, size, name };
  } catch (e) {
    await db.runAsync(`UPDATE downloads SET status='failed' WHERE url=?`, url);
    throw e;
  }
}

export async function listDownloads(): Promise<{ url: string; name: string; size: number | null; local_uri: string | null; sha256: string | null; status: string; walk_id: string | null }[]> {
  return (await getDb()).getAllAsync('SELECT * FROM downloads ORDER BY rowid DESC');
}
