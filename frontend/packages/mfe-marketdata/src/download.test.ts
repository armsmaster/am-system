import { afterEach, describe, expect, it, vi } from 'vitest';

import { saveFile } from './download';

describe('saveFile', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('downloads the file under its name and releases the link later', () => {
    vi.useFakeTimers();
    const create = vi.fn(() => 'blob:calc');
    const revoke = vi.fn();
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      expect([this.href, this.download]).toEqual(['blob:calc', 'Расчёт.xlsx']);
    });
    const file = new Blob(['PK']);

    saveFile(file, 'Расчёт.xlsx');

    expect(create).toHaveBeenCalledWith(file);
    expect(click).toHaveBeenCalledOnce();
    expect(revoke).not.toHaveBeenCalled();
    vi.advanceTimersByTime(60_000);
    expect(revoke).toHaveBeenCalledWith('blob:calc');
  });
});
