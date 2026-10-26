import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useLoad } from './hooks';

describe('useLoad', () => {
  it('keeps old data on screen while reloading', async () => {
    let value = 1;
    const load = vi.fn(() => Promise.resolve(value));
    const { result } = renderHook(() => useLoad(load));
    await waitFor(() => expect(result.current[0]).toEqual({ status: 'ready', data: 1 }));

    value = 2;
    act(() => result.current[1]());

    expect(result.current[0]).toEqual({ status: 'ready', data: 1 });
    await waitFor(() => expect(result.current[0]).toEqual({ status: 'ready', data: 2 }));
  });

  it('ignores a response that arrives after unmount', async () => {
    let resolve: (value: number) => void = () => undefined;
    const load = vi.fn(() => new Promise<number>((r) => (resolve = r)));
    const { result, unmount } = renderHook(() => useLoad(load));

    unmount();
    resolve(42);
    await new Promise((r) => setTimeout(r, 0));

    expect(result.current[0]).toEqual({ status: 'loading' });
  });
});
