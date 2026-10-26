import { ApiError } from '@am/platform';
import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useAction, useLoad } from './hooks';

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

  it('turns a failure into a message for the user', async () => {
    const load = () => Promise.reject(new ApiError(503, { detail: { message: 'ISS недоступен' } }));
    const { result } = renderHook(() => useLoad(load));

    await waitFor(() =>
      expect(result.current[0]).toEqual({ status: 'failed', message: 'ISS недоступен' }),
    );
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

describe('useAction', () => {
  it('reports success and reloads', async () => {
    const onDone = vi.fn();
    const { result } = renderHook(() => useAction(onDone));

    let ok = false;
    await act(async () => {
      ok = await result.current.run(() => Promise.resolve('готово'));
    });

    expect(ok).toBe(true);
    expect(onDone).toHaveBeenCalledOnce();
    expect(result.current).toMatchObject({ error: null, busy: false });
  });

  it('keeps the data and shows why the server refused', async () => {
    const onDone = vi.fn();
    const { result } = renderHook(() => useAction(onDone));

    let ok = true;
    await act(async () => {
      ok = await result.current.run(() =>
        Promise.reject(new ApiError(422, { detail: { message: 'Так нельзя' } })),
      );
    });

    expect(ok).toBe(false);
    expect(onDone).not.toHaveBeenCalled();
    expect(result.current).toMatchObject({ error: 'Так нельзя', busy: false });
  });

  it('is busy while the action runs', async () => {
    let finish: () => void = () => undefined;
    const { result } = renderHook(() => useAction(() => undefined));

    let running: Promise<boolean> = Promise.resolve(true);
    act(() => {
      running = result.current.run(() => new Promise<void>((r) => (finish = r)));
    });
    expect(result.current.busy).toBe(true);

    await act(async () => {
      finish();
      await running;
    });
    expect(result.current.busy).toBe(false);
  });
});
