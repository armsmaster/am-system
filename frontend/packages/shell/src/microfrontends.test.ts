import { beforeEach, describe, expect, it, vi } from 'vitest';

const runtime = vi.hoisted(() => ({
  registerRemotes: vi.fn(),
  loadRemote: vi.fn(),
}));

vi.mock('@module-federation/enhanced/runtime', () => runtime);

const {
  loadAdmin,
  loadAudit,
  loadMarketdata,
  loadBooking,
  loadReconciliation,
  loadRefdata,
  microfrontendRemotes,
  registerMicrofrontends,
} = await import('./microfrontends');

describe('microfrontendRemotes', () => {
  it('serves remotes from the same origin by default', () => {
    expect(microfrontendRemotes({}, 'https://am.example')).toEqual([
      { name: 'mfe_admin', entry: 'https://am.example/mfe/admin/mf-manifest.json' },
      { name: 'mfe_audit', entry: 'https://am.example/mfe/audit/mf-manifest.json' },
      { name: 'mfe_refdata', entry: 'https://am.example/mfe/refdata/mf-manifest.json' },
      { name: 'mfe_marketdata', entry: 'https://am.example/mfe/marketdata/mf-manifest.json' },
      { name: 'mfe_booking', entry: 'https://am.example/mfe/booking/mf-manifest.json' },
      {
        name: 'mfe_reconciliation',
        entry: 'https://am.example/mfe/reconciliation/mf-manifest.json',
      },
    ]);
  });

  it('can point a remote to its dev server', () => {
    const [admin, audit, refdata, marketdata, booking, reconciliation] = microfrontendRemotes(
      {
        PUBLIC_MFE_ADMIN_URL: 'http://localhost:3001/',
        PUBLIC_MFE_AUDIT_URL: 'http://localhost:3002/',
        PUBLIC_MFE_REFDATA_URL: 'http://localhost:3003/',
        PUBLIC_MFE_MARKETDATA_URL: 'http://localhost:3004/',
        PUBLIC_MFE_BOOKING_URL: 'http://localhost:3005/',
        PUBLIC_MFE_RECONCILIATION_URL: 'http://localhost:3006/',
      },
      'http://localhost:3000',
    );

    expect(admin?.entry).toBe('http://localhost:3001/mf-manifest.json');
    expect(audit?.entry).toBe('http://localhost:3002/mf-manifest.json');
    expect(refdata?.entry).toBe('http://localhost:3003/mf-manifest.json');
    expect(marketdata?.entry).toBe('http://localhost:3004/mf-manifest.json');
    expect(booking?.entry).toBe('http://localhost:3005/mf-manifest.json');
    expect(reconciliation?.entry).toBe('http://localhost:3006/mf-manifest.json');
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

  it('loads every microfrontend by its name', async () => {
    const module = { default: () => null };
    runtime.loadRemote.mockResolvedValue(module);

    expect(await loadAdmin()).toBe(module);
    expect(await loadAudit()).toBe(module);
    expect(await loadRefdata()).toBe(module);
    expect(await loadMarketdata()).toBe(module);
    expect(await loadBooking()).toBe(module);
    expect(await loadReconciliation()).toBe(module);
    expect(runtime.loadRemote.mock.calls).toEqual([
      ['mfe_admin/App'],
      ['mfe_audit/App'],
      ['mfe_refdata/App'],
      ['mfe_marketdata/App'],
      ['mfe_booking/App'],
      ['mfe_reconciliation/App'],
    ]);
  });

  it('fails if the remote returns nothing', async () => {
    runtime.loadRemote.mockResolvedValue(undefined);

    await expect(loadAdmin()).rejects.toThrow('mfe_admin/App is not available');
  });
});
