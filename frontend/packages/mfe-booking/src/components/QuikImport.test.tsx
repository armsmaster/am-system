import { ApiError } from '@am/platform';
import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { fakeApi, quikLine, quikReport, renderBooking } from '../test/fixtures';

const CONTENT = '<HTML>Отчёт по всем сделкам клиента</HTML>';

function report(content = CONTENT): File {
  return new File([content], 'Отчёт №1.xls', { type: 'application/vnd.ms-excel' });
}

/** Тот же файл в base64, как его отправит форма. */
function encoded(content = CONTENT): string {
  const bytes = new TextEncoder().encode(content);
  return btoa(String.fromCharCode(...bytes));
}

async function open(api = fakeApi()) {
  const user = userEvent.setup();
  renderBooking(api, '/booking/import');
  const form = within(await screen.findByRole('form', { name: 'Загрузка из отчёта QUIK' }));
  return { api, user, form };
}

describe('QuikImport', () => {
  it('checks the report first, then loads it', async () => {
    const { api, user, form } = await open();
    expect(form.getByRole('button', { name: 'Проверить' })).toBeDisabled();

    await user.upload(form.getByLabelText('Отчёт QUIK'), report());
    await user.selectOptions(form.getByLabelText('Портфель'), 'p-bonds');
    await user.click(form.getByRole('button', { name: 'Проверить' }));

    expect(api.importQuik).toHaveBeenCalledWith({
      file: encoded(),
      portfolio_id: 'p-bonds',
      outright_rate: null,
      repo_rate: null,
      apply: false,
    });
    expect(
      await screen.findByText(
        'Отчёт за 27.11.2026 — 30.11.2026, код клиента 00000T_01. Проверка, ещё не загружено: новых сделок — 2, ' +
          'новых версий — 1, без изменений — 1, отклонено — 2.',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Тариф счёта с 01.11.2026: купля-продажа — 0,0125% от оборота с НКД, РЕПО — 0,5% годовых.'),
    ).toBeInTheDocument();
    const rows = within(screen.getByRole('table', { name: 'Сделки отчёта' })).getAllByRole('row');
    expect(rows[0]).toHaveTextContent('Что будет');
    expect(rows[1]).toHaveTextContent('118100000001');
    expect(rows[1]).toHaveTextContent('RU000A1038V6 ОФЗ 26238покупка10052,556 001,007,00 (0,0125%)новая сделка');
    expect(rows[2]).toHaveTextContent('продажа');
    expect(rows[2]).toHaveTextContent('без изменений Продажа больше позиции.');
    expect(rows[3]).toHaveTextContent('Бумаги RU000A10FV69 нет в справочнике.');
    expect(rows[4]).toHaveTextContent('618100000006— —————Строку не разобрать.');
    expect(rows[5]).toHaveTextContent('обратное РЕПО——30 000 000,001 232,88 (0,5%)новая сделка');
    expect(rows[6]).toHaveTextContent('прямое РЕПО');

    api.importQuik.mockResolvedValueOnce(
      quikReport({ applied: true, lines: [quikLine({ trade_id: 't-9' })] }),
    );
    await user.click(screen.getByRole('button', { name: 'Загрузить' }));

    expect(api.importQuik).toHaveBeenLastCalledWith(expect.objectContaining({ apply: true }));
    expect(await screen.findByText(/Загружено: новых сделок — 1/)).toBeInTheDocument();
    const loaded = within(screen.getByRole('table', { name: 'Сделки отчёта' })).getAllByRole('row');
    expect(loaded[0]).toHaveTextContent('Результат');
    expect(within(loaded[1]!).getByRole('link', { name: '18100000001' })).toHaveAttribute(
      'href',
      '/booking/trades/t-9',
    );
    expect(screen.queryByRole('button', { name: 'Загрузить' })).not.toBeInTheDocument();
  });

  it('sends rates for this upload and asks to check again after a change', async () => {
    const { api, user, form } = await open();
    await user.upload(form.getByLabelText('Отчёт QUIK'), report());
    await user.type(form.getByLabelText('Купля-продажа, % от оборота с НКД'), '0,02');
    await user.type(form.getByLabelText('РЕПО, % годовых'), ' 0,6 ');
    await user.click(form.getByRole('button', { name: 'Проверить' }));

    expect(api.importQuik).toHaveBeenCalledWith(expect.objectContaining({ outright_rate: '0.02', repo_rate: '0.6' }));
    expect(await screen.findByRole('button', { name: 'Загрузить' })).toBeInTheDocument();

    // Поменяли ставку — прежняя проверка уже не про эти ставки.
    await user.clear(form.getByLabelText('РЕПО, % годовых'));
    expect(screen.queryByRole('button', { name: 'Загрузить' })).not.toBeInTheDocument();
  });

  it('has nothing to load when every trade is already there', async () => {
    const api = fakeApi();
    api.importQuik.mockResolvedValue(
      quikReport({ tariff: null, period_from: null, client_code: null, lines: [quikLine({ result: 'unchanged' })] }),
    );
    const { user, form } = await open(api);
    await user.upload(form.getByLabelText('Отчёт QUIK'), report());
    await user.click(form.getByRole('button', { name: 'Проверить' }));

    expect(await screen.findByText(/^Отчёт\. Проверка/)).toBeInTheDocument();
    expect(screen.getByText(/Тарифа брокера у счёта нет/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Загрузить' })).toBeDisabled();
    expect(screen.getByText(/Загружать нечего/)).toBeInTheDocument();
  });

  it('asks to add new instruments first and does not load until then', async () => {
    const api = fakeApi();
    api.importQuik.mockResolvedValue(
      quikReport({ new_instruments: [{ code: 'RU000A10FV69', name: 'РЖД 1Р-54R' }] }),
    );
    const { user, form } = await open(api);
    await user.upload(form.getByLabelText('Отчёт QUIK'), report());
    await user.click(form.getByRole('button', { name: 'Проверить' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('В отчёте бумаги, которых нет в справочнике. Сначала заведите их');
    expect(within(alert).getByRole('link', { name: '«Справочник»' })).toHaveAttribute('href', '/refdata');
    expect(within(alert).getByRole('list', { name: 'Новые бумаги' })).toHaveTextContent('RU000A10FV69 РЖД 1Р-54R');
    expect(screen.getByRole('button', { name: 'Загрузить' })).toBeDisabled();
  });

  it('explains a file the service refused', async () => {
    const api = fakeApi();
    api.importQuik.mockRejectedValue(
      new ApiError(422, { detail: { code: 'not_quik_report', message: 'Это не отчёт QUIK по сделкам клиента.' } }),
    );
    const { user, form } = await open(api);
    await user.upload(form.getByLabelText('Отчёт QUIK'), report());
    await user.click(form.getByRole('button', { name: 'Проверить' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Отчёт №1.xls: Это не отчёт QUIK по сделкам клиента.');
  });

  it('shows progress and ignores an empty choice', async () => {
    const api = fakeApi();
    api.importQuik.mockReturnValue(new Promise(() => undefined));
    const { user, form } = await open(api);

    fireEvent.change(form.getByLabelText('Отчёт QUIK'), { target: { files: [] } });
    fireEvent.submit(screen.getByRole('form', { name: 'Загрузка из отчёта QUIK' }));
    expect(api.importQuik).not.toHaveBeenCalled();

    await user.upload(form.getByLabelText('Отчёт QUIK'), report());
    await user.click(form.getByRole('button', { name: 'Проверить' }));
    expect(await screen.findByText('Читаем Отчёт №1.xls…')).toBeInTheDocument();
  });

  it('says when there is no portfolio to load into', async () => {
    const api = fakeApi();
    api.portfolios.mockResolvedValue([]);
    renderBooking(api, '/booking/import');

    expect(await screen.findByText(/загружать отчёт некуда/)).toBeInTheDocument();
  });

  it('switches to the JSON batch', async () => {
    const { user } = await open();
    await user.click(screen.getByRole('tab', { name: 'Пакет JSON' }));
    expect(screen.getByLabelText('Файл со сделками')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: 'Отчёт QUIK' }));
    expect(screen.getByRole('form', { name: 'Загрузка из отчёта QUIK' })).toBeInTheDocument();
  });
});
