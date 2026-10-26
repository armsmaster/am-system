import { ApiError } from '@am/platform';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { asApi, fakeApi, LOOKUP, params, renderPage, SUMMARY, type FakeApi } from '../test/fixtures';
import { InstrumentsPage } from './InstrumentsPage';

function renderList(api: FakeApi = fakeApi(), canLoad = true) {
  renderPage(<InstrumentsPage api={asApi(api)} basePath="/refdata" canLoad={canLoad} />);
  return api;
}

async function findInIss(code: string) {
  await userEvent.type(screen.getByLabelText('Код бумаги'), code);
  await userEvent.click(screen.getByRole('button', { name: 'Найти в MOEX ISS' }));
}

describe('InstrumentsPage: list', () => {
  it('lists instruments with their codes, names, kind and last check against ISS', async () => {
    const api = fakeApi();
    api.search.mockResolvedValue([
      SUMMARY,
      {
        ...SUMMARY,
        id: 'i-2',
        secid: 'TMOS',
        isin: null,
        kind: 'fund_share',
        short_name: 'Тинькофф iMOEX',
        name: 'БПИФ Тинькофф Индекс МосБиржи',
        last_sync_error: 'ISS недоступен',
      },
    ]);
    renderList(api);

    const rows = (await screen.findAllByRole('row')).slice(1);
    const cells = rows.map((row) => within(row).getAllByRole('cell').map((c) => c.textContent));
    expect(cells).toEqual([
      ['SU26238RMFS4RU000A1038V6', 'ОФЗ 26238ОФЗ-ПД 26238 15/05/2041', 'облигация', '26.10.2026, 06:00'],
      ['TMOS', 'Тинькофф iMOEXБПИФ Тинькофф Индекс МосБиржи', 'пай БПИФ', 'не удалась'],
    ]);

    await userEvent.click(screen.getByRole('link', { name: 'SU26238RMFS4' }));
    expect(screen.getByText('Открыта карточка i-1')).toBeInTheDocument();
  });

  it('invites to load the first instrument into an empty reference', async () => {
    const api = fakeApi();
    api.search.mockResolvedValue([]);
    renderList(api);

    expect(await screen.findByText(/пока нет бумаг/)).toBeInTheDocument();
  });

  it('searches as you type, ignoring spaces around', async () => {
    const api = fakeApi();
    renderList(api);
    await screen.findByRole('link', { name: 'SU26238RMFS4' });
    api.search.mockResolvedValue([]);

    await userEvent.type(screen.getByLabelText('Поиск по справочнику'), ' 29025 ');

    expect(await screen.findByText(/Ничего не нашлось/)).toBeInTheDocument();
    expect(api.search).toHaveBeenLastCalledWith('29025');
  });
});

describe('InstrumentsPage: loading from ISS', () => {
  it('previews what ISS has and loads it, then opens the card', async () => {
    const api = renderList();

    await findInIss(' SU26238RMFS4 ');

    expect(api.lookup).toHaveBeenCalledWith('SU26238RMFS4');
    const preview = within(screen.getByRole('region', { name: 'Предпросмотр' }));
    expect(preview.getByRole('heading')).toHaveTextContent('SU26238RMFS4 — ОФЗ 26238');
    const facts = preview.getAllByRole('definition').map((dd) => dd.textContent);
    expect(facts).toEqual([
      'ОФЗ-ПД 26238 15/05/2041',
      'RU000A1038V6',
      'облигация',
      'Министерство финансов Российской Федерации',
      '1 000 RUB',
      '15.05.2041',
      'фиксированный',
      'купонов: 2, оферт: 0, амортизаций: 1',
    ]);

    await userEvent.click(preview.getByRole('button', { name: 'Загрузить в справочник' }));

    expect(api.load).toHaveBeenCalledWith('SU26238RMFS4');
    expect(await screen.findByText('Открыта карточка i-1')).toBeInTheDocument();
  });

  it('shows a fund share without bond terms', async () => {
    const api = fakeApi();
    api.lookup.mockResolvedValue({
      secid: 'TMOS',
      isin: null,
      kind: 'fund_share',
      params: params({ short_name: 'Тинькофф iMOEX', issuer: null, coupon_kind: null }),
      instrument_id: null,
    });
    renderList(api);

    await findInIss('TMOS');

    const preview = within(screen.getByRole('region', { name: 'Предпросмотр' }));
    expect(preview.getByText('пай БПИФ')).toBeInTheDocument();
    expect(preview.queryByText('Дата погашения')).not.toBeInTheDocument();
    expect(preview.getAllByText('—')).toHaveLength(2);
  });

  it('opens the card if the instrument is already in the reference', async () => {
    const api = fakeApi();
    api.lookup.mockResolvedValue({ ...LOOKUP, instrument_id: 'i-1' });
    renderList(api);

    await findInIss('SU26238RMFS4');
    expect(screen.getByText(/уже в справочнике/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Загрузить в справочник' })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('link', { name: 'Открыть карточку' }));
    expect(screen.getByText('Открыта карточка i-1')).toBeInTheDocument();
  });

  it('lets only trading and operations load', async () => {
    renderList(fakeApi(), false);

    await findInIss('SU26238RMFS4');

    expect(screen.getByText(/могут трейдинг и сопровождение/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Загрузить в справочник' })).not.toBeInTheDocument();
  });

  it('offers similar securities when ISS has no exact match', async () => {
    const api = fakeApi();
    api.lookup.mockRejectedValueOnce(
      new ApiError(404, {
        detail: {
          code: 'not_in_iss',
          message: 'В MOEX ISS нет бумаги с кодом «26238».',
          candidates: [
            {
              secid: 'SU26238RMFS4',
              isin: 'RU000A1038V6',
              short_name: 'ОФЗ 26238',
              name: 'ОФЗ-ПД 26238',
              type_name: 'Государственная облигация',
            },
            { secid: 'X26238', isin: null, short_name: 'Другая', name: 'Другая', type_name: null },
          ],
        },
      }),
    );
    renderList(api);

    await findInIss('26238');

    expect(screen.getByRole('alert')).toHaveTextContent('нет бумаги с кодом «26238»');
    const similar = within(screen.getByRole('list', { name: 'Похожие бумаги' }));
    expect(similar.getByText('RU000A1038V6, Государственная облигация')).toBeInTheDocument();

    await userEvent.click(similar.getByRole('button', { name: 'SU26238RMFS4' }));

    expect(api.lookup).toHaveBeenLastCalledWith('SU26238RMFS4');
    expect(screen.getByLabelText('Код бумаги')).toHaveValue('SU26238RMFS4');
    expect(await screen.findByRole('region', { name: 'Предпросмотр' })).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Похожие бумаги' })).not.toBeInTheDocument();
  });

  it('says why loading failed and lets retry', async () => {
    const api = fakeApi();
    api.load.mockRejectedValueOnce(
      new ApiError(503, { detail: { code: 'iss_unavailable', message: 'MOEX ISS недоступен.' } }),
    );
    renderList(api);
    await findInIss('SU26238RMFS4');

    await userEvent.click(screen.getByRole('button', { name: 'Загрузить в справочник' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('MOEX ISS недоступен.');
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Загрузить в справочник' })).toBeEnabled(),
    );
  });
});
