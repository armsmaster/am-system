import { ApiError } from '@am/platform';
import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { fakeApi, renderReconciliation, statement } from '../test/fixtures';

async function openStatements(api = fakeApi(), isOperations = true) {
  renderReconciliation(api, '/reconciliation/statements', isOperations);
  fireEvent.change(await screen.findByLabelText('День'), { target: { value: '2026-12-02' } });
  return api;
}

describe('StatementsPage', () => {
  it('shows the current statement and the ones it replaced', async () => {
    const api = fakeApi();
    api.statements.mockResolvedValue([
      statement({
        closing_cash: '46911949.95',
        movements: [
          { kind: 'coupon', amount: '885000.00', trade: null, code: 'RU000A1038V6', comment: null },
          { kind: 'settlement', amount: '-28000500.00', trade: '1000000001', code: null, comment: 'Покупка' },
        ],
      }),
      statement({ id: 's-0', source: 'manual', received_by: 'ops' }),
    ]);
    await openStatements(api);

    const current = await screen.findByRole('article', { name: 'Действующая выписка' });
    expect(api.statements).toHaveBeenLastCalledWith('a-broker', '2026-12-02');
    expect(current).toHaveTextContent('источник broker-x, service-account-am-statement-feed');
    expect(current).toHaveTextContent('Остаток на начало 46 026 949,95 ₽, на конец 46 911 949,95 ₽. Сделок: 0.');
    expect(current).toHaveTextContent('купон RU000A1038V6: 885 000,00 ₽');
    expect(current).toHaveTextContent('расчёты по сделке 1000000001: −28 000 500,00 ₽ — Покупка');
    expect(screen.getByRole('article', { name: 'Прежняя выписка' })).toHaveTextContent('введена вручную, ops');

    await userEvent.click(within(current).getByRole('button', { name: 'Пакет, как пришёл' }));
    expect(api.batch).toHaveBeenCalledWith('b-1');
    expect(await screen.findByLabelText('Пакет выписок')).toHaveTextContent('"account": "OPIF1-BROKER"');
  });

  it('says when the batch cannot be shown and when there is nothing', async () => {
    const api = fakeApi();
    api.batch.mockRejectedValue(new ApiError(404, { detail: { code: 'not_found', message: 'Не найдено: batch' } }));
    await openStatements(api);

    await userEvent.click(await screen.findByRole('button', { name: 'Пакет, как пришёл' }));
    expect(await screen.findByText('Не найдено: batch')).toBeInTheDocument();

    api.statements.mockResolvedValue([]);
    await userEvent.selectOptions(screen.getByLabelText('Счёт'), 'a-bank');
    expect(await screen.findByText('Выписок за этот день нет.')).toBeInTheDocument();
    expect(api.statements).toHaveBeenLastCalledWith('a-bank', '2026-12-02');
  });

  it('enters a bank statement by hand', async () => {
    const api = await openStatements();
    const form = await screen.findByRole('form', { name: 'Ввод выписки' });
    const submit = within(form).getByRole('button', { name: 'Ввести выписку' });
    expect(submit).toBeDisabled();

    await userEvent.type(within(form).getByLabelText('Остаток на начало, ₽'), '40 000 000');
    await userEvent.type(within(form).getByLabelText('Остаток на конец, ₽'), '39 979 450');
    await userEvent.click(within(form).getByRole('button', { name: 'Добавить движение' }));
    await userEvent.click(within(form).getByRole('button', { name: 'Добавить движение' }));
    const first = within(form).getByRole('group', { name: 'Движение 1' });
    await userEvent.selectOptions(within(first).getByLabelText('Вид'), 'coupon');
    await userEvent.type(within(first).getByLabelText('Бумага'), 'SU26238RMFS4');
    await userEvent.type(within(first).getByLabelText('Номер сделки'), '2000000001');
    // Вид сменили — бумага и номер сделки, которых у вида нет, не уходят.
    await userEvent.selectOptions(within(first).getByLabelText('Вид'), 'fee_payment');
    expect(within(first).queryByLabelText('Бумага')).not.toBeInTheDocument();
    await userEvent.type(within(first).getByLabelText(/Сумма/), '−20 550');
    await userEvent.type(within(first).getByLabelText('Комментарий'), 'Вознаграждение УК за ноябрь');
    await userEvent.click(within(within(form).getByRole('group', { name: 'Движение 2' })).getByRole('button', { name: 'Убрать движение' }));
    await userEvent.click(submit);

    expect(api.enter).toHaveBeenCalledWith({
      account_id: 'a-broker',
      date: '2026-12-02',
      opening_cash: '40000000',
      closing_cash: '39979450',
      movements: [
        { kind: 'fee_payment', amount: '-20550', trade: null, security: null, comment: 'Вознаграждение УК за ноябрь' },
      ],
    });
    expect(await screen.findByText('Выписка принята.')).toBeInTheDocument();
    expect(api.statements).toHaveBeenCalledTimes(3);
  });

  it('sends a coupon with its security and a settlement with its trade', async () => {
    const api = await openStatements();
    api.enter.mockResolvedValueOnce({ statement_id: 's-3', result: 'replaced' });
    const form = await screen.findByRole('form', { name: 'Ввод выписки' });

    await userEvent.type(within(form).getByLabelText('Остаток на начало, ₽'), '0');
    await userEvent.type(within(form).getByLabelText('Остаток на конец, ₽'), '0');
    await userEvent.click(within(form).getByRole('button', { name: 'Добавить движение' }));
    await userEvent.click(within(form).getByRole('button', { name: 'Добавить движение' }));
    const [coupon, settlement] = within(form).getAllByRole('group');
    await userEvent.selectOptions(within(coupon!).getByLabelText('Вид'), 'coupon');
    await userEvent.type(within(coupon!).getByLabelText('Бумага'), ' SU26238RMFS4 ');
    await userEvent.type(within(coupon!).getByLabelText(/Сумма/), '885000');
    await userEvent.selectOptions(within(settlement!).getByLabelText('Вид'), 'settlement');
    await userEvent.type(within(settlement!).getByLabelText('Номер сделки'), '1000000001');
    await userEvent.type(within(settlement!).getByLabelText(/Сумма/), '-885000');
    await userEvent.click(within(form).getByRole('button', { name: 'Ввести выписку' }));

    expect(api.enter).toHaveBeenCalledWith(
      expect.objectContaining({
        movements: [
          { kind: 'coupon', amount: '885000', trade: null, security: 'SU26238RMFS4', comment: null },
          { kind: 'settlement', amount: '-885000', trade: '1000000001', security: null, comment: null },
        ],
      }),
    );
    expect(await screen.findByText('Выписка принята и заменила прежнюю за этот день.')).toBeInTheDocument();
  });

  it('shows what the service said', async () => {
    const api = await openStatements();
    const form = await screen.findByRole('form', { name: 'Ввод выписки' });
    await userEvent.type(within(form).getByLabelText('Остаток на начало, ₽'), '1');
    await userEvent.type(within(form).getByLabelText('Остаток на конец, ₽'), '1');
    await userEvent.click(within(form).getByRole('button', { name: 'Добавить движение' }));
    await userEvent.selectOptions(within(form).getByLabelText('Вид'), 'redemption');

    api.enter.mockResolvedValueOnce({ statement_id: 's-1', result: 'unchanged' });
    await userEvent.click(within(form).getByRole('button', { name: 'Ввести выписку' }));
    expect(await screen.findByText('Такая выписка уже есть — ничего не изменилось.')).toBeInTheDocument();
    // Бумагу не указали — проверит сервис: у погашения она обязательна.
    expect(api.enter).toHaveBeenLastCalledWith(
      expect.objectContaining({
        movements: [{ kind: 'redemption', amount: '', trade: null, security: null, comment: null }],
      }),
    );

    api.enter.mockRejectedValueOnce(
      new ApiError(422, { detail: { code: 'statement_does_not_add_up', message: 'Не хватает движений на −2 800,05 ₽.' } }),
    );
    await userEvent.click(within(form).getByRole('button', { name: 'Ввести выписку' }));
    expect(await screen.findByText('Не хватает движений на −2 800,05 ₽.')).toBeInTheDocument();
  });

  it('shows the trader only the history', async () => {
    await openStatements(fakeApi(), false);

    expect(await screen.findByRole('article', { name: 'Действующая выписка' })).toBeInTheDocument();
    expect(screen.queryByRole('form', { name: 'Ввод выписки' })).not.toBeInTheDocument();
  });

  it('says when there is no account', async () => {
    const api = fakeApi();
    api.accounts.mockResolvedValue([]);
    renderReconciliation(api, '/reconciliation/statements');

    expect(await screen.findByText('Нет счетов, к которым у вас есть доступ.')).toBeInTheDocument();
  });
});
