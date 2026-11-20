import * as Crypto from 'expo-crypto';

export function toHex(buf: ArrayBuffer | Uint8Array): string {
  const u = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < u.length; i++) s += u[i].toString(16).padStart(2, '0');
  return s;
}

/** Small buffers (thumbnails, notes). */
export async function sha256Bytes(data: Uint8Array): Promise<string> {
  return toHex(await Crypto.digest(Crypto.CryptoDigestAlgorithm.SHA256, data as any));
}

/**
 * Whole-file hash. expo-crypto only offers one-shot digests (the whole buffer in memory), which cannot hold a
 * multi-GB .insv, so files go through the native streaming File.digest('SHA-256') in expo-file-system.
 */
export async function sha256File(file: { digest(alg: 'SHA-256'): Promise<string> }): Promise<string> {
  return file.digest('SHA-256');
}
