import { afterEach, describe, expect, it, vi } from 'vitest';

import { saveFile } from './download';

describe('saveFile', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('downloads the file under the given name and frees the link a minute later', () => {
    vi.useFakeTimers();
    URL.createObjectURL = vi.fn(() => 'blob:file');
    const revoke = vi.fn();
    URL.revokeObjectURL = revoke;
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      expect([this.href, this.download]).toEqual(['blob:file', 'СЧА.xlsx']);
    });

    saveFile(new Blob(['x']), 'СЧА.xlsx');

    expect(click).toHaveBeenCalledOnce();
    expect(revoke).not.toHaveBeenCalled();
    vi.advanceTimersByTime(60_000);
    expect(revoke).toHaveBeenCalledWith('blob:file');
  });
});
