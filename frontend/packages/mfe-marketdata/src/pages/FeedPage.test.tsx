import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { asApi, BATCH, fakeApi } from '../test/fixtures';
import { FeedPage } from './FeedPage';

describe('FeedPage', () => {
  it('lists batches from the script and why items were rejected', async () => {
    const api = fakeApi();
    api.batches.mockResolvedValue([BATCH, { ...BATCH, id: 'b-2', kind: 'bars', accepted: 10, rejected: [] }]);
    render(<FeedPage api={asApi(api)} />);

    const rows = (await screen.findAllByRole('row')).slice(1);
    expect(rows.map((r) => within(r).getAllByRole('cell').map((c) => c.textContent))).toEqual([
      [
        '27.10, 11:05',
        'снимки цен',
        'quikservice-account-am-price-feed',
        '3',
        '№2 XS0000000000: нет в справочникеБумаги XS0000000000 нет в справочнике.',
      ],
      ['27.10, 11:05', 'бары', 'quikservice-account-am-price-feed', '10', '—'],
    ]);
    expect(rows[0]).toHaveClass('wide');
  });

  it('shows an unknown error code as is', async () => {
    const api = fakeApi();
    api.batches.mockResolvedValue([
      { ...BATCH, rejected: [{ ...BATCH.rejected[0]!, error: 'new_check' }] },
    ]);
    render(<FeedPage api={asApi(api)} />);

    expect(await screen.findByText(/new_check/)).toBeInTheDocument();
  });

  it('says when the script sent nothing yet', async () => {
    const api = fakeApi();
    api.batches.mockResolvedValue([]);
    render(<FeedPage api={asApi(api)} />);

    expect(await screen.findByText('Скрипт ещё ничего не присылал.')).toBeInTheDocument();
  });
});
