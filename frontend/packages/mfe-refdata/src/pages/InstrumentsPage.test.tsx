import { ApiError } from '@am/platform';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { InstrumentSummary } from '../api';
import { asApi, fakeApi, LOOKUP, params, renderPage, SUMMARY, type FakeApi } from '../test/fixtures';
import { InstrumentsPage } from './InstrumentsPage';

function renderList(api: FakeApi = fakeApi(), canLoad = true) {
  renderPage(<InstrumentsPage api={asApi(api)} basePath="/refdata" canLoad={canLoad} />);
  return api;
}

async function findInIss(code: string) {
  // Поиск в ISS — в выдвижной панели: открываем её, если ещё закрыта.
  if (!screen.queryByLabelText('Код бумаги')) {
    await userEvent.click(await screen.findByRole('button', { name: /^(Загрузить из|Найти в) ISS$/ }));
  }
  await userEvent.type(screen.getByLabelText('Код бумаги'), code);
  await userEvent.click(screen.getByRole('button', { name: 'Найти в MOEX ISS' }));
}

function cells() {
  return within(screen.getByRole('table', { name: 'Бумаги' }))
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell').map((c) => c.textContent));
}

/** Бумаги всех видов: фикс, флоатер с офертой, линкер, пай, бумага без типа купона. */
const ITEMS: InstrumentSummary[] = [
  SUMMARY,
  {
    ...SUMMARY,
    id: 'i-2',
    secid: 'RU000A107HG1',
    isin: 'RU000A107HG1',
    short_name: 'Газпнф3P8R',
    issuer_name: 'Публичное акционерное общество "Газпром нефть"',
    coupon_kind: 'floater',
    coupon_rate: '17.3',
    floater_rate: 'key_rate',
    floater_margin_bp: '130.00',
    maturity_date: '2026-12-12',
    next_offer_date: '2026-11-25',
    last_synced_at: null,
  },
  {
    ...SUMMARY,
    id: 'i-3',
    secid: 'RU000A10F504',
    short_name: 'ВЭБ2Р-58',
    issuer_name: null,
    nominal_indexation: 'ruonia',
    coupon_rate: '1.85',
  },
  {
    ...SUMMARY,
    id: 'i-4',
    secid: 'LQDT',
    kind: 'fund_share',
    short_name: 'ВИМ Ликвидность',
    coupon_kind: null,
    coupon_rate: null,
    maturity_date: null,
    last_sync_error: 'ISS недоступен',
  },
  { ...SUMMARY, id: 'i-5', secid: 'RU000A0JTZF1', short_name: 'ДОМ.РФ', coupon_kind: 'unknown', coupon_rate: null },
];

describe('InstrumentsPage: list', () => {
  it('shows the short name, the coupon in brief, the issuer and the check against ISS', async () => {
    const api = fakeApi();
    api.search.mockResolvedValue(ITEMS);
    renderList(api);

    await screen.findByRole('table', { name: 'Бумаги' });
    expect(cells()).toEqual([
      ['ОФЗ 26238SU26238RMFS4фикс 7,1% · 15.05.2041', 'Минфин России', '15.05.2041', '26.10.2026, 06:00'],
      // Оферта впереди: в колонке «Погашение» второй строкой, на телефоне — в подписи вместо погашения.
      ['Газпнф3P8RRU000A107HG1КС + 130 бп · оферта 25.11.2026', 'ПАО «Газпром нефть»', '12.12.2026оферта 25.11.2026', '—'],
      ['ВЭБ2Р-58RU000A10F504линкер на RUONIA · 1,85% · 15.05.2041', '—', '15.05.2041', '26.10.2026, 06:00'],
      ['ВИМ ЛиквидностьLQDTпай фонда', 'Минфин России', '—', 'не удалась'],
      ['ДОМ.РФRU000A0JTZF1тип купона неизвестен · 15.05.2041', 'Минфин России', '15.05.2041', '26.10.2026, 06:00'],
    ]);
    expect(screen.getByText('не удалась').closest('span')).toHaveAttribute('title', 'ISS недоступен');

    // Карточку открывает и ссылка с именем, и щелчок по строке.
    await userEvent.click(screen.getByRole('link', { name: 'ОФЗ 26238' }));
    expect(screen.getByText('Открыта карточка i-1')).toBeInTheDocument();
  });

  it('opens the card on a click anywhere in the row', async () => {
    renderList();

    await userEvent.click(await screen.findByText('Минфин России'));

    expect(screen.getByText('Открыта карточка i-1')).toBeInTheDocument();
  });

  it('filters by the coupon and hides empty filters', async () => {
    const api = fakeApi();
    api.search.mockResolvedValue(ITEMS);
    renderList(api);
    const chips = within(await screen.findByRole('group', { name: 'Купон' }));

    expect(chips.getAllByRole('button').map((b) => b.textContent)).toEqual([
      'Все 5',
      'Фиксы 1',
      'Флоатеры 1',
      'Линкеры 1',
      'Паи 1',
      'Прочие 1',
    ]);
    await userEvent.click(chips.getByRole('button', { name: 'Флоатеры 1' }));
    expect(cells().map((row) => row[0])).toEqual(['Газпнф3P8RRU000A107HG1КС + 130 бп · оферта 25.11.2026']);

    // Поиск оставил только фикс: выбранный фильтр остаётся и говорит, что таких нет.
    api.search.mockResolvedValue([SUMMARY]);
    await userEvent.type(screen.getByLabelText('Поиск по справочнику'), '26238');
    expect(await screen.findByText('Таких бумаг в списке нет.')).toBeInTheDocument();
    expect(chips.getAllByRole('button').map((b) => b.textContent)).toEqual(['Все 1', 'Фиксы 1', 'Флоатеры 0']);
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
    await screen.findByRole('link', { name: 'ОФЗ 26238' });
    api.search.mockResolvedValue([]);

    await userEvent.type(screen.getByLabelText('Поиск по справочнику'), ' 29025 ');

    expect(await screen.findByText(/Ничего не нашлось/)).toBeInTheDocument();
    expect(api.search).toHaveBeenLastCalledWith('29025');
  });

  it('opens loading from ISS in a side panel and closes it', async () => {
    renderList();

    await userEvent.click(await screen.findByRole('button', { name: 'Загрузить из ISS' }));
    expect(screen.getByRole('dialog', { name: 'Загрузить из ISS' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
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
