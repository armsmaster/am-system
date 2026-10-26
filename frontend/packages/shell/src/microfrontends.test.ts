import { beforeEach, describe, expect, it, vi } from 'vitest';

const runtime = vi.hoisted(() => ({
  registerRemotes: vi.fn(),
  loadRemote: vi.fn(),
}));

vi.mock('@module-federation/enhanced/runtime', () => runtime);

const { loadAdmin, loadAudit, microfrontendRemotes, registerMicrofrontends } = await import(
  './microfrontends'
);

describe('microfrontendRemotes', () => {
  it('serves remotes from the same origin by default', () => {
    expect(microfrontendRemotes({}, 'https://am.example')).toEqual([
      { name: 'mfe_admin', entry: 'https://am.example/mfe/admin/mf-manifest.json' },
      { name: 'mfe_audit', entry: 'https://am.example/mfe/audit/mf-manifest.json' },
    ]);
  });

  it('can point a remote to its dev server', () => {
    const [admin, audit] = microfrontendRemotes(
      { PUBLIC_MFE_ADMIN_URL: 'http://localhost:3001/', PUBLIC_MFE_AUDIT_URL: 'http://localhost:3002/' },
      'http://localhost:3000',
    );

    expect(admin?.entry).toBe('http://localhost:3001/mf-manifest.json');
    expect(audit?.entry).toBe('http://localhost:3002/mf-manifest.json');
  });
});

describe('loading', () => {
  beforeEach(() => {
    runtime.registerRemotes.mockClear();
    runtime.loadRemote.mockReset();
  });

  it('registers remotes for the current origin', () => {
    registerMicrofrontends();

    expect(runtime.registerRemotes).toHaveBeenCalledWith(microfrontendRemotes());
  });

  it('loads the admin and audit microfrontends', async () => {
    const module = { default: () => null };
    runtime.loadRemote.mockResolvedValue(module);

    expect(await loadAdmin()).toBe(module);
    expect(await loadAudit()).toBe(module);
    expect(runtime.loadRemote.mock.calls).toEqual([['mfe_admin/App'], ['mfe_audit/App']]);
  });

  it('fails if the remote returns nothing', async () => {
    runtime.loadRemote.mockResolvedValue(undefined);

    await expect(loadAdmin()).rejects.toThrow('mfe_admin/App is not available');
  });
});
