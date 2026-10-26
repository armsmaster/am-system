import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { asApi, fakeApi, ruleViolation } from '../test/fixtures';
import { AccountsPage } from './AccountsPage';

function form(name: string) {
  return within(screen.getByRole('form', { name }));
}

describe('AccountsPage', () => {
  it('lists accounts with fund, cash account and markets', async () => {
    render(<AccountsPage api={asApi(fakeApi())} />);

    const rows = await screen.findAllByRole('row');
    const cells = rows.slice(1).map((row) => within(row).getAllByRole('cell').map((c) => c.textContent));
    expect(cells).toEqual([
      ['A1 Счёт A1', 'F1', 'брокерский, Брокер: 30601', 'D-1', 'фондовый'],
      ['A1-BANK Счёт A1-BANK', 'F1', 'банковский, Банк: 30601', '—', '—'],
      ['A2 Счёт A2', 'F2', 'брокерский, Брокер: 30601', 'D-1', 'фондовый, срочный'],
    ]);
  });

  it('says when there are no accounts or funds yet', async () => {
    render(<AccountsPage api={asApi(fakeApi({ accounts: [], portfolios: [] }))} />);

    expect(await screen.findByText('Счетов пока нет.')).toBeInTheDocument();
    expect(screen.getByText(/сначала создайте фонд/)).toBeInTheDocument();
  });

  it('shows a fund code placeholder for an unknown fund', async () => {
    render(<AccountsPage api={asApi(fakeApi({ portfolios: [] }))} />);

    const [first] = (await screen.findAllByRole('row')).slice(1);
    expect(within(first!).getAllByRole('cell')[1]).toHaveTextContent('—');
  });

  it('edits the selected account', async () => {
    const api = fakeApi();
    render(<AccountsPage api={asApi(api)} />);
    expect(await screen.findByText('Выберите счёт, чтобы изменить его.')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'A1' }));
    const edit = form('Изменить счёт');
    await userEvent.clear(edit.getByLabelText('Счёт депо (если есть)'));
    await userEvent.click(edit.getByLabelText('Срочный рынок'));
    await userEvent.click(edit.getByRole('button', { name: 'Сохранить' }));

    expect(api.updateAccount).toHaveBeenCalledWith('id-A1', {
      code: 'A1',
      name: 'Счёт A1',
      cash_kind: 'broker',
      institution: 'Брокер',
      cash_account: '30601',
      depo_account: null,
      stock_market: true,
      derivatives_market: true,
    });
    expect(screen.getByRole('row', { name: /A1 Счёт A1 F1/ })).toHaveClass('selected');
  });

  it('creates an account for the chosen fund', async () => {
    const api = fakeApi();
    render(<AccountsPage api={asApi(api)} />);
    const create = within(await screen.findByRole('form', { name: 'Новый счёт' }));

    await userEvent.selectOptions(create.getByLabelText('Фонд'), 'id-F2');
    await userEvent.type(create.getByLabelText('Код'), 'A2-BANK');
    await userEvent.type(create.getByLabelText('Название'), 'Расчётный счёт');
    await userEvent.selectOptions(create.getByLabelText('Денежный счёт'), 'bank');
    await userEvent.type(create.getByLabelText('Брокер или банк'), 'Банк');
    await userEvent.type(create.getByLabelText('Номер денежного счёта'), '40701');
    await userEvent.type(create.getByLabelText('Счёт депо (если есть)'), 'D-9');
    await userEvent.click(create.getByLabelText('Фондовый рынок'));
    await userEvent.click(create.getByRole('button', { name: 'Создать' }));

    expect(api.createAccount).toHaveBeenCalledWith({
      fund_id: 'id-F2',
      code: 'A2-BANK',
      name: 'Расчётный счёт',
      cash_kind: 'bank',
      institution: 'Банк',
      cash_account: '40701',
      depo_account: 'D-9',
      stock_market: false,
      derivatives_market: false,
    });
    expect(create.getByLabelText('Код')).toHaveValue('');
  });

  it('keeps the new account form when the server refuses', async () => {
    const api = fakeApi();
    api.createAccount.mockRejectedValue(ruleViolation('Код счёта «A1» уже занят.'));
    render(<AccountsPage api={asApi(api)} />);
    const create = within(await screen.findByRole('form', { name: 'Новый счёт' }));

    await userEvent.type(create.getByLabelText('Код'), 'A1');
    await userEvent.type(create.getByLabelText('Название'), 'Дубль');
    await userEvent.type(create.getByLabelText('Брокер или банк'), 'Брокер');
    await userEvent.type(create.getByLabelText('Номер денежного счёта'), '1');
    await userEvent.click(create.getByRole('button', { name: 'Создать' }));

    expect(await create.findByRole('alert')).toHaveTextContent('уже занят');
    expect(create.getByLabelText('Код')).toHaveValue('A1');
  });
});
