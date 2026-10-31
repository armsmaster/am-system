import { ApiError } from '@am/platform';
import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { fakeApi, renderBooking } from '../test/fixtures';

const BATCH = { source: 'broker-x', trades: [{ external_id: 'BX-0042' }] };

function file(content: string, name = 'trades.json'): File {
  return new File([content], name, { type: 'application/json' });
}

async function upload(content: string, api = fakeApi()) {
  const user = userEvent.setup();
  renderBooking(api, '/booking/import');
  await user.upload(await screen.findByLabelText('Файл со сделками'), file(content));
  return api;
}

describe('ImportPage', () => {
  it('sends the batch and shows what was accepted and what was rejected', async () => {
    const api = await upload(JSON.stringify(BATCH));

    expect(api.importTrades).toHaveBeenCalledWith(BATCH);
    expect(
      await screen.findByText(
        'trades.json: новых сделок — 1, новых версий — 0, отменено — 0, без изменений — 1, отклонено — 1.',
      ),
    ).toBeInTheDocument();
    const rejected = within(screen.getByRole('table', { name: 'Отклонено' })).getAllByRole('row');
    expect(rejected[1]).toHaveTextContent('2BX-0051Бумаги LQDT нет в справочнике');
    const accepted = within(screen.getByRole('table', { name: 'Принято' })).getAllByRole('row');
    expect(accepted[1]).toHaveTextContent('1BX-0042без изменений—');
    expect(accepted[2]).toHaveTextContent('3BX-0017новая сделкаПродажа больше позиции');
    expect(within(accepted[2]!).getByRole('link', { name: 'BX-0017' })).toHaveAttribute(
      'href',
      '/booking/trades/t-2',
    );
  });

  it('shows only the tables that have rows', async () => {
    const api = fakeApi();
    api.importTrades.mockResolvedValue({ created: 0, amended: 0, cancelled: 0, unchanged: 0, trades: [], rejected: [] });
    await upload(JSON.stringify(BATCH), api);

    expect(await screen.findByText(/новых сделок — 0/)).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('explains a file that is not JSON', async () => {
    const api = await upload('source: broker-x');

    expect(await screen.findByRole('alert')).toHaveTextContent('trades.json: Файл — не JSON');
    expect(api.importTrades).not.toHaveBeenCalled();
  });

  it('lists schema errors: nothing is accepted', async () => {
    const api = fakeApi();
    api.importTrades.mockRejectedValue(
      new ApiError(422, {
        detail: [
          { loc: ['body', 'trades', 2, 'price'], msg: 'Input should be greater than 0' },
          { loc: ['body', 'source'], msg: 'String should match pattern' },
          { loc: ['body'], msg: 'Field required' },
          {},
        ],
      }),
    );
    await upload(JSON.stringify(BATCH), api);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Файл не соответствует спецификации — не принято ничего.');
    expect(within(alert).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'сделка 3, price: Input should be greater than 0',
      'source: String should match pattern',
      'пакет: Field required',
      'пакет: ',
    ]);
  });

  it.each([
    [new ApiError(503, { detail: { code: 'refdata_unavailable', message: 'Справочник недоступен.' } }), 'Справочник недоступен.'],
    [new ApiError(422, { detail: { code: 'x', message: 'Не так.' } }), 'Не так.'],
    [new ApiError(403, null), 'Недостаточно прав'],
  ])('shows a refusal of the service', async (error, text) => {
    const api = fakeApi();
    api.importTrades.mockRejectedValue(error);
    await upload(JSON.stringify(BATCH), api);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(text);
    expect(within(alert).queryByRole('list')).not.toBeInTheDocument();
  });

  it('shows progress and ignores an empty choice', async () => {
    const api = fakeApi();
    api.importTrades.mockReturnValue(new Promise(() => undefined));
    renderBooking(api, '/booking/import');
    const input = await screen.findByLabelText('Файл со сделками');

    fireEvent.change(input, { target: { files: [] } });
    expect(screen.queryByText(/Загружаем/)).not.toBeInTheDocument();
    await userEvent.setup().upload(input, file(JSON.stringify(BATCH), 'day.json'));
    expect(await screen.findByText('Загружаем day.json…')).toBeInTheDocument();
    expect(fireEvent.submit(screen.getByRole('form', { name: 'Загрузка сделок' }))).toBe(false);
  });
});
