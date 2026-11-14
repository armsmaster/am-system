import { ApiError } from '@am/platform';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it } from 'vitest';

import { asApi, fakeApi, floaterParams, instrument, params, version, type FakeApi } from '../test/fixtures';
import { InstrumentPage } from './InstrumentPage';

/** Бумага с двумя версиями: из ISS и с правкой сопровождения. */
const EDITED = instrument(
  [
    version(1),
    version(2, {
      valid_from: '2026-10-01',
      source: 'manual',
      author: 'ops',
      comment: 'Краткое название по проспекту',
      created_at: '2026-10-21T09:00:00Z',
      pinned: ['short_name'],
      params: params({ short_name: 'ОФЗ-ПД 26238' }),
    }),
  ],
  {
    alerts: [
      { code: 'differs_from_iss', message: 'Поле «Краткое название» закреплено вручную и расходится с ISS.' },
    ],
  },
);

function renderCard(api: FakeApi = fakeApi(), canEdit = false, canRate = false) {
  render(
    <MemoryRouter initialEntries={['/refdata/instruments/i-1']}>
      <Routes>
        <Route
          path="/refdata/instruments/:id"
          element={
            <InstrumentPage
              api={asApi(api)}
              id="i-1"
              basePath="/refdata"
              canEdit={canEdit}
              canRate={canRate}
              today="2026-11-13"
            />
          }
        />
        <Route path="/refdata" element={<p>Список бумаг</p>} />
        <Route path="/refdata/issuers/:id" element={<p>Карточка эмитента</p>} />
      </Routes>
    </MemoryRouter>,
  );
  return api;
}

async function tiles() {
  const list = within(await screen.findByLabelText('Ключевые параметры'));
  const terms = list.getAllByRole('term').map((dt) => dt.textContent);
  const values = list.getAllByRole('definition').map((dd) => dd.textContent);
  return Object.fromEntries(terms.map((term, i) => [term, values[i]]));
}

function rows(name: string) {
  return within(screen.getByRole('table', { name }))
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell').map((c) => c.textContent));
}

const tab = (name: RegExp) => userEvent.click(screen.getByRole('tab', { name }));

describe('InstrumentPage', () => {
  it('shows the instrument, its key terms, alerts and coupons', async () => {
    const api = fakeApi();
    api.instrument.mockResolvedValue(EDITED);
    renderCard(api);

    expect(await screen.findByText('ОФЗ-ПД 26238', { selector: '.tag' })).toBeInTheDocument();
    expect(
      screen.getByText(/^ОФЗ-ПД 26238 15\/05\/2041 · RU000A1038V6 · облигация · ОФЗ · эмитент:/),
    ).toHaveTextContent('эмитент: Министерство финансов Российской Федерации · сверка с ISS 26.10.2026, 06:00');
    expect(screen.getByRole('list', { name: 'Предупреждения' })).toHaveTextContent(
      'закреплено вручную и расходится с ISS',
    );
    expect(await tiles()).toEqual({
      Номинал: '1 000 ₽',
      Погашение: '15.05.2041',
      Купон: '7,1% · 182D',
      'Ближайший купон': '25.11.2026 · 35,4 ₽',
    });
    expect(screen.getByRole('tab', { name: 'Купоны 2' })).toHaveAttribute('aria-selected', 'true');
    expect(rows('Купоны')[0]![0]).toBe('25.11.2026 ближайшийфиксация 24.11.2026');
    expect(api.version).not.toHaveBeenCalled();
  });

  it('lists versions newest first and shows the parameters of the latest', async () => {
    const api = fakeApi();
    api.instrument.mockResolvedValue(EDITED);
    renderCard(api);
    await tab(await screen.findByRole('tab', { name: 'Версии 2' }).then(() => /^Версии/));

    expect(rows('Версии')).toEqual([
      ['2', '01.10.2026', 'вручнуюops, 21.10.2026, 12:00', 'Краткое название по проспектуЗакреплено: Краткое название'],
      ['1', 'с начала обращения', 'MOEX ISStrader, 20.10.2026, 10:15', 'Загружена из MOEX ISS'],
    ]);
    expect(screen.getByRole('row', { name: /^2 01\.10\.2026/ })).toHaveClass('selected');
    await tab(/^Параметры/);
    expect(screen.getByRole('region', { name: 'Версия 2' })).toBeInTheDocument();
  });

  it('shows an older version on request and returns to the latest without asking the server', async () => {
    const api = fakeApi();
    api.instrument.mockResolvedValue(EDITED);
    api.version.mockResolvedValue(version(1));
    renderCard(api);
    const latest = await screen.findByRole('button', { name: 'Версия 2, с 01.10.2026, последняя' });
    expect(latest).toHaveAttribute('aria-pressed', 'true');

    // Версию выбирают в ленте над плитками…
    await userEvent.click(screen.getByRole('button', { name: 'Версия 1, с начала обращения' }));

    expect(api.version).toHaveBeenCalledWith('i-1', 1);
    expect(await screen.findByText(/Показана версия 1 — не последняя/)).toBeInTheDocument();
    await tab(/^Параметры/);
    expect(screen.getByRole('heading', { name: 'Версия 1: с начала обращения' })).toBeInTheDocument();

    // …или строкой в истории версий.
    await tab(/^Версии/);
    expect(screen.getByRole('row', { name: /^1 с начала обращения/ })).toHaveClass('selected');
    await userEvent.click(screen.getByRole('row', { name: /^2 01\.10\.2026/ }));
    expect(screen.queryByText(/Показана версия/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('row', { name: /^1 с начала обращения/ }));
    await userEvent.click(await screen.findByRole('button', { name: 'К последней версии' }));

    expect(latest).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByText(/Показана версия/)).not.toBeInTheDocument();
    // Версию 1 выбирали дважды; последнюю сервер не спрашивают — она уже в карточке.
    expect(api.version.mock.calls).toEqual([
      ['i-1', 1],
      ['i-1', 1],
    ]);
  });

  it('tells who made each version in the tooltip of the version bar', async () => {
    const api = fakeApi();
    api.instrument.mockResolvedValue(instrument([version(1, { comment: null }), version(2)]));
    renderCard(api);

    const bar = within(await screen.findByRole('group', { name: 'Версия бумаги' }));
    // Новые слева.
    expect(bar.getAllByRole('button').map((b) => b.getAttribute('title'))).toEqual([
      'MOEX ISS, trader — Загружена из MOEX ISS',
      'MOEX ISS, trader',
    ]);
  });

  it('hides editing from those who may not edit', async () => {
    const api = fakeApi();
    api.instrument.mockResolvedValue(instrument([version(1, { comment: null })]));
    renderCard(api);
    await screen.findByRole('tab', { name: 'Версии 1' });
    await tab(/^Версии/);

    expect(rows('Версии')[0]?.[3]).toBe('—');
    // Одна версия — ленты версий нет.
    expect(screen.queryByRole('group', { name: 'Версия бумаги' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Обновить из ISS' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Изменить' })).not.toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Предупреждения' })).not.toBeInTheDocument();
  });

  it('gives operations the edit form in a side panel', async () => {
    renderCard(fakeApi(), true);

    await userEvent.click(await screen.findByRole('button', { name: 'Изменить' }));

    expect(within(screen.getByRole('dialog', { name: 'Изменить бумагу' })).getByRole('form', { name: 'Правка' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows when the last check against ISS failed', async () => {
    const api = fakeApi();
    api.instrument.mockResolvedValue(instrument(undefined, { last_sync_error: 'ISS недоступен' }));
    renderCard(api);

    expect(await screen.findByRole('list', { name: 'Предупреждения' })).toHaveTextContent(
      'Последняя сверка с ISS не удалась: ISS недоступен',
    );
  });

  it('refreshes from ISS: says so when nothing changed', async () => {
    const api = renderCard(fakeApi(), true);

    await userEvent.click(await screen.findByRole('button', { name: 'Обновить из ISS' }));

    expect(api.sync).toHaveBeenCalledWith('i-1');
    expect(await screen.findByRole('status')).toHaveTextContent('совпадают с последней версией');
    expect(api.instrument).toHaveBeenCalledTimes(2);
  });

  it('refreshes from ISS: opens the new version when ISS changed the instrument', async () => {
    const api = fakeApi();
    const changed = instrument([
      version(1),
      version(2, {
        valid_from: '2026-10-26',
        author: 'ops',
        comment: 'Синхронизация с MOEX ISS: изменено — Купоны',
        params: floaterParams(),
      }),
    ]);
    api.instrument.mockResolvedValueOnce(instrument()).mockResolvedValue(changed);
    renderCard(api, true);

    await userEvent.click(await screen.findByRole('button', { name: 'Обновить из ISS' }));

    expect(await screen.findByRole('tab', { name: 'Версии 2' })).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('says why the refresh failed', async () => {
    const api = fakeApi();
    api.sync.mockRejectedValue(
      new ApiError(503, { detail: { code: 'iss_unavailable', message: 'MOEX ISS недоступен.' } }),
    );
    renderCard(api, true);

    await userEvent.click(await screen.findByRole('button', { name: 'Обновить из ISS' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('MOEX ISS недоступен.');
  });

  it('says when the instrument could not be loaded', async () => {
    const api = fakeApi();
    api.instrument.mockRejectedValue(new ApiError(404, { detail: { message: 'Не найдено: instrument' } }));
    renderCard(api);

    expect(await screen.findByRole('alert')).toHaveTextContent('Не найдено: instrument');
  });

  it('shows redemptions, offers and the key terms of a linker', async () => {
    const api = fakeApi();
    const linker = params({
      nominal_indexation: 'ruonia',
      coupons: [{ date: '2026-11-01', start_date: null, record_date: null, rate: '1.85', amount: '10.18' }],
      offers: [{ date: '2028-05-17', kind: 'put', window_start: null, window_end: null, price: '100', iss_type: null }],
    });
    api.instrument.mockResolvedValue(instrument([version(1, { params: linker })]));
    renderCard(api);
    await screen.findByRole('tab', { name: 'Оферты 1' });

    expect(await tiles()).toMatchObject({
      Номинал: '1 000 ₽, индексируется',
      Купон: '1,85%',
      'Ближайший купон': 'купонов впереди нет',
      Оферта: '17.05.2028',
    });
    await tab(/^Погашение номинала/);
    expect(screen.getByRole('table', { name: 'Погашение номинала' })).toBeInTheDocument();
    await tab(/^Оферты/);
    expect(rows('Оферты')[0]![0]).toBe('17.05.2028приём заявок не указан');
  });

  it('says that the next coupon is not announced yet', async () => {
    const api = fakeApi();
    // Ближайший — второй купон: его сумма ещё не объявлена.
    api.instrument.mockResolvedValue(
      instrument([version(1, { params: params({ coupons: params().coupons.slice(1) }) })]),
    );
    renderCard(api);

    expect((await tiles())['Ближайший купон']).toBe('26.05.2027 · не объявлен');
  });

  it('shows the issuer, ratings and classifiers of the bond on their own tabs', async () => {
    const api = renderCard();
    await screen.findByRole('tab', { name: 'Рейтинги' });

    await tab(/^Рейтинги/);
    expect(await screen.findByRole('list', { name: 'Действующие рейтинги' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Значения сейчас')).not.toBeInTheDocument();
    expect(api.ratings).toHaveBeenCalledWith({ instrument_id: 'i-1' });
    // Вести рейтинги может только риск-менеджмент.
    expect(screen.queryByRole('button', { name: 'Добавить запись' })).not.toBeInTheDocument();

    await tab(/^Классификаторы/);
    expect(await screen.findByLabelText('Значения сейчас')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Рейтинги' })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('link', { name: 'Министерство финансов Российской Федерации' }));
    expect(screen.getByText('Карточка эмитента')).toBeInTheDocument();
  });

  it('gives risk management the rating and classifier forms', async () => {
    renderCard(fakeApi(), false, true);
    await screen.findByRole('tab', { name: 'Рейтинги' });

    await tab(/^Рейтинги/);
    expect(await screen.findByRole('button', { name: 'Добавить запись' })).toBeInTheDocument();

    await tab(/^Классификаторы/);
    expect(await screen.findByRole('button', { name: 'Задать значение' })).toBeInTheDocument();
  });

  it('shows a fund share: no issuer yet, no coupons, no ratings', async () => {
    const api = fakeApi();
    api.instrument.mockResolvedValue(instrument([version(1)], { kind: 'fund_share', issuer: null, iss_type: null }));
    renderCard(api);

    expect(await screen.findByText(/появится после сверки с MOEX ISS/)).toBeInTheDocument();
    expect((await screen.findAllByRole('tab')).map((t) => t.textContent)).toEqual([
      'Параметры',
      'Версии 1',
      'Классификаторы',
    ]);
    expect(await tiles()).toEqual({ Валюта: 'RUB', 'Режим торгов': 'TQOB' });
    await tab(/^Классификаторы/);
    expect(await screen.findByLabelText('Значения сейчас')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Рейтинги' })).not.toBeInTheDocument();
    expect(api.ratings).not.toHaveBeenCalled();
  });

  it('leads back to the list', async () => {
    renderCard();

    await userEvent.click(screen.getByRole('link', { name: '← Бумаги' }));

    expect(screen.getByText('Список бумаг')).toBeInTheDocument();
  });
});
