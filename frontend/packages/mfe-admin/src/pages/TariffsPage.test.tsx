import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { asApi, fakeApi, ruleViolation } from '../test/fixtures';
import { TariffsPage } from './TariffsPage';

describe('TariffsPage', () => {
  it('shows the tariff history of a broker account, the latest first', async () => {
    const api = fakeApi();
    render(<TariffsPage api={asApi(api)} />);

    const rows = within(await screen.findByRole('table', { name: 'Тарифы счёта' })).getAllByRole('row');
    expect(rows.slice(1).map((r) => r.textContent)).toEqual([
      '01.11.20260,01251adminНовый тариф брокера',
      '01.01.20260,0150,5admin—',
    ]);
    expect(api.tariffs).toHaveBeenCalledWith('id-A1');
    // Банковский счёт — не брокерский: его в списке нет.
    const options = within(screen.getByLabelText('Брокерский счёт')).getAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual(['A1 — Счёт A1', 'A2 — Счёт A2']);

    await userEvent.selectOptions(screen.getByLabelText('Брокерский счёт'), 'id-A2');
    expect(api.tariffs).toHaveBeenLastCalledWith('id-A2');
  });

  it('saves a new tariff', async () => {
    const api = fakeApi({ tariffs: [] });
    const user = userEvent.setup();
    render(<TariffsPage api={asApi(api)} />);

    expect(await screen.findByText(/Тарифа у счёта нет/)).toBeInTheDocument();
    const form = within(screen.getByRole('form', { name: 'Новый тариф' }));
    await user.type(form.getByLabelText('Купля-продажа, % от оборота без НКД'), ' 0,0125');
    await user.type(form.getByLabelText('РЕПО, % годовых'), '0,5');
    await user.type(form.getByLabelText('Действует с'), '2026-11-01');
    await user.click(form.getByRole('button', { name: 'Сохранить тариф' }));

    expect(api.setTariff).toHaveBeenCalledWith('id-A1', {
      outright_rate: '0.0125',
      repo_rate: '0.5',
      valid_from: '2026-11-01',
      comment: null,
    });
    expect(api.tariffs).toHaveBeenCalledTimes(2);

    await user.type(form.getByLabelText('Комментарий'), 'Тариф с ноября');
    api.setTariff.mockRejectedValueOnce(ruleViolation('Счёта нет или к его портфелям нет доступа.'));
    await user.click(form.getByRole('button', { name: 'Сохранить тариф' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Счёта нет');
    expect(api.setTariff).toHaveBeenLastCalledWith('id-A1', expect.objectContaining({ comment: 'Тариф с ноября' }));
  });

  it('explains when there are no broker accounts', async () => {
    render(<TariffsPage api={asApi(fakeApi({ accounts: [] }))} />);

    expect(await screen.findByText(/Брокерских счетов пока нет/)).toBeInTheDocument();
  });

  it('keeps whole percentages as they are', async () => {
    const api = fakeApi({
      tariffs: [
        { outright_rate: '1', repo_rate: '10.000000', valid_from: '2026-11-01', comment: null, author: 'admin', created_at: 'x' },
      ],
    });
    render(<TariffsPage api={asApi(api)} />);

    const rows = within(await screen.findByRole('table', { name: 'Тарифы счёта' })).getAllByRole('row');
    expect(rows[1]).toHaveTextContent('01.11.2026110admin');
  });
});
