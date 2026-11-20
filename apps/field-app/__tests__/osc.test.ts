import { OscClient, OscError, supports } from '../src/lib/osc/client';
import { applyPreset, presetOptions } from '../src/lib/osc/preset';

type Handler = (body: any) => { status?: number; json: any; headers?: Record<string, string> };

/** Fake camera: routes by `${method} ${path}`; commands by name. */
function fakeCamera(opts: { options: Record<string, any>; rejectSet?: (o: Record<string, any>) => string | null; inProgress?: boolean }) {
  const calls: { path: string; body: any }[] = [];
  const set: Record<string, any>[] = [];
  let polls = 0;
  const fetchImpl = async (url: string, init: any) => {
    const path = new URL(url).pathname;
    const body = init?.body ? JSON.parse(init.body) : undefined;
    calls.push({ path, body });
    const res = (json: any, status = 200) => ({ ok: status < 400, status, headers: { get: () => null }, json: async () => json, arrayBuffer: async () => new ArrayBuffer(0) });
    if (path === '/osc/info') return res({ manufacturer: 'Insta360', model: 'ONE X2', apiLevel: [2] });
    if (path === '/osc/state') return res({ state: { batteryLevel: 0.8, _captureStatus: 'idle' } });
    if (path === '/osc/commands/status') {
      polls++;
      return res({ id: 'c1', name: 'camera.takePicture', state: polls >= 2 ? 'done' : 'inProgress', results: { fileUrl: 'http://192.168.42.1/DCIM/IMG_1.insp' } });
    }
    if (path === '/osc/commands/execute') {
      switch (body.name) {
        case 'camera.getOptions': {
          const out: Record<string, any> = {};
          for (const n of body.parameters.optionNames) if (n in opts.options) out[n] = opts.options[n];
          return res({ name: body.name, state: 'done', results: { options: out } });
        }
        case 'camera.setOptions': {
          const why = opts.rejectSet?.(body.parameters.options);
          if (why) return res({ name: body.name, state: 'error', error: { code: 'invalidParameterValue', message: why } }, 400);
          set.push(body.parameters.options);
          return res({ name: body.name, state: 'done' });
        }
        case 'camera.takePicture':
          return opts.inProgress ? res({ id: 'c1', name: body.name, state: 'inProgress' }) : res({ name: body.name, state: 'done', results: { fileUrl: 'x.insp' } });
        case 'camera.listFiles': {
          const first = !body.parameters.continuationToken;
          return res({ state: 'done', results: { entries: [{ name: first ? 'a.insv' : 'b.insp', fileUrl: first ? 'http://10.0.0.9/DCIM/a.insv' : 'DCIM/b.insp', size: 5 }], continuationToken: first ? 't2' : undefined } });
        }
      }
    }
    return res({ error: { code: 'unknownCommand', message: 'nope' } }, 400);
  };
  return { fetchImpl: fetchImpl as any, calls, set };
}

describe('supports()', () => {
  it('range, list, object subset and undecidable', () => {
    expect(supports({ minSupport: 0.001, maxSupport: 0.1 }, 0.004)).toBe(true);
    expect(supports({ minSupport: 0.001, maxSupport: 0.1 }, 1)).toBe(false);
    expect(supports([1, 2, 4], 4)).toBe(true);
    expect(supports([0.004], 1 / 250)).toBe(true);
    expect(supports([{ type: 'mp4', width: 5760, height: 2880, framerate: 30, extra: 1 }], { type: 'mp4', width: 5760, height: 2880, framerate: 30 })).toBe(true);
    expect(supports([{ type: 'mp4', width: 3840, height: 1920, framerate: 30 }], { type: 'mp4', width: 5760, height: 2880, framerate: 30 })).toBe(false);
    expect(supports(undefined, 1)).toBeNull();
  });
});

describe('OscClient', () => {
  it('reads info and state via the expected endpoints', async () => {
    const cam = fakeCamera({ options: {} });
    const c = new OscClient({ fetchImpl: cam.fetchImpl });
    expect(c.base).toBe('http://192.168.42.1:80');
    expect((await c.info()).model).toBe('ONE X2');
    expect((await c.state()).state?.batteryLevel).toBe(0.8);
    expect(cam.calls.map((x) => x.path)).toEqual(['/osc/info', '/osc/state']);
  });

  it('polls /osc/commands/status until done', async () => {
    const cam = fakeCamera({ options: {}, inProgress: true });
    const c = new OscClient({ fetchImpl: cam.fetchImpl, sleep: async () => {} });
    expect(await c.takePicture()).toBe('http://192.168.42.1/DCIM/IMG_1.insp');
    expect(cam.calls.filter((x) => x.path === '/osc/commands/status')).toHaveLength(2);
  });

  it('raises OscError with the camera code on command error', async () => {
    const cam = fakeCamera({ options: { captureMode: 'image', captureModeSupport: ['image'] }, rejectSet: () => 'bad value' });
    const c = new OscClient({ fetchImpl: cam.fetchImpl });
    await expect(c.execute('camera.setOptions', { options: { a: 1 } })).rejects.toMatchObject({ name: 'OscError', code: 'invalidParameterValue' });
  });

  it('turns network failure into an unreachable OscError', async () => {
    const c = new OscClient({ fetchImpl: (async () => { throw new TypeError('Network request failed'); }) as any });
    await expect(c.info()).rejects.toMatchObject({ code: 'unreachable' });
  });

  it('setOptions applies supported, refuses unsupported/unknown with reasons', async () => {
    const cam = fakeCamera({ options: { captureMode: 'video', captureModeSupport: ['image', 'video'], iso: 100, isoSupport: [100, 200] } });
    const c = new OscClient({ fetchImpl: cam.fetchImpl });
    const r = await c.setOptions({ captureMode: 'video', iso: 800, whiteBalanceLock: true });
    expect(r.applied).toEqual({ captureMode: 'video' });
    expect(r.refused.iso).toMatch(/not in camera support list/);
    expect(r.refused.whiteBalanceLock).toMatch(/does not expose/);
    expect(cam.set).toEqual([{ captureMode: 'video' }]);
  });

  it('requireSupport refuses options with no support list; batch rejection falls back to one by one', async () => {
    const cam = fakeCamera({
      options: { shutterSpeed: 0.01, exposureProgram: 2, exposureProgramSupport: [1, 2, 4] },
      rejectSet: (o) => (Object.keys(o).length > 1 ? 'batch refused' : 'shutterSpeed' in o ? 'camera locked' : null),
    });
    const c = new OscClient({ fetchImpl: cam.fetchImpl });
    const r = await c.setOptions({ shutterSpeed: 0.004, exposureProgram: 4 }, { requireSupport: true });
    expect(r.refused.shutterSpeed).toMatch(/did not list supported values/);
    expect(r.applied).toEqual({ exposureProgram: 4 });
  });

  it('listFiles pages with continuationToken and resolveFileUrl pins the camera host', async () => {
    const cam = fakeCamera({ options: {} });
    const c = new OscClient({ fetchImpl: cam.fetchImpl });
    const files = await c.listFiles();
    expect(files.map((f) => f.name)).toEqual(['a.insv', 'b.insp']);
    expect(c.resolveFileUrl('http://10.0.0.9/DCIM/a.insv')).toBe('http://192.168.42.1:80/DCIM/a.insv');
    expect(c.resolveFileUrl('DCIM/b.insp')).toBe('http://192.168.42.1:80/DCIM/b.insp');
  });
});

describe('construction preset', () => {
  it('video is 5.7K 30fps, photo is HDR', () => {
    expect(presetOptions('video')).toMatchObject({ captureMode: 'video', fileFormat: { width: 5760, height: 2880, framerate: 30 } });
    expect(presetOptions('photo')).toMatchObject({ captureMode: 'image', hdr: 'hdr' });
  });

  it('shutter and exposure are applied only when listed, otherwise reported as refused', async () => {
    const withSupport = fakeCamera({ options: { captureMode: 'video', captureModeSupport: ['video'], fileFormat: {}, fileFormatSupport: [{ type: 'mp4', width: 5760, height: 2880, framerate: 30 }], exposureProgram: 2, exposureProgramSupport: [2, 4], shutterSpeed: 0.01, shutterSpeedSupport: [0.004, 0.01] } });
    const r1 = await applyPreset(new OscClient({ fetchImpl: withSupport.fetchImpl }), 'video');
    expect(Object.keys(r1.applied).sort()).toEqual(['captureMode', 'exposureProgram', 'fileFormat', 'shutterSpeed']);

    const without = fakeCamera({ options: { captureMode: 'video', captureModeSupport: ['video'], fileFormat: {}, fileFormatSupport: [{ type: 'mp4', width: 5760, height: 2880, framerate: 30 }] } });
    const r2 = await applyPreset(new OscClient({ fetchImpl: without.fetchImpl }), 'video');
    expect(r2.applied).not.toHaveProperty('shutterSpeed');
    expect(r2.refused.shutterSpeed).toBeDefined();
    expect(r2.refused.exposureProgram).toBeDefined();
  });
});

it('OscError carries code', () => {
  expect(new OscError('x', 'c').code).toBe('c');
});
