import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { closing, fakeApi, PORTFOLIOS, renderReconciliation } from '../test/fixtures';

describe('ClosingsPage', () => {
  it('shows who closed and reopened days, latest first', async () => {
    const api = renderReconciliation(fakeApi(), '/reconciliation/closings');

    const rows = within(await screen.findByRole('table', { name: 'Закрытые дни' })).getAllByRole('row');
    expect(api.closing).toHaveBeenCalledWith('p-fund');
    expect(screen.getByText('Фонд закрыт по 01.12.2026. Следующий к закрытию — 02.12.2026.')).toBeInTheDocument();
    expect(rows[1]).toHaveTextContent('закрыты30.11.202630.11.2026ops');
    expect(rows[2]).toHaveTextContent('открыты заново30.11.202629.11.2026admin');
    expect(rows[2]).toHaveTextContent('Брокер исправил отчёт');
    // Понедельник закрыли вместе с выходными.
    expect(rows[3]).toHaveTextContent('закрыты28.11.2026 – 30.11.202630.11.2026ops');
    expect(rows[3]).toHaveTextContent('—');
  });

  it('says when nothing is closed and switches funds', async () => {
    const api = fakeApi();
    api.portfolios.mockResolvedValue([...PORTFOLIOS, { id: 'p-fund-2', code: 'OPIF2', name: 'ОПИФ 2', is_fund: true }]);
    api.closing.mockResolvedValue(closing({ closed_through: null, next_day: null, history: [] }));
    renderReconciliation(api, '/reconciliation/closings');

    expect(await screen.findByText('Закрытых дней нет.')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText('Фонд'), 'p-fund-2');
    expect(api.closing).toHaveBeenLastCalledWith('p-fund-2');
  });

  it('says when there is no fund', async () => {
    const api = fakeApi();
    api.portfolios.mockResolvedValue([PORTFOLIOS[1]!]);
    renderReconciliation(api, '/reconciliation/closings');

    expect(await screen.findByText('Нет фондов, к которым у вас есть доступ.')).toBeInTheDocument();
  });
});
