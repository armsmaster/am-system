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
            <InstrumentPage api={asApi(api)} id="i-1" basePath="/refdata" canEdit={canEdit} canRate={canRate} />
          }
        />
        <Route path="/refdata" element={<p>Список бумаг</p>} />
        <Route path="/refdata/issuers/:id" element={<p>Карточка эмитента</p>} />
      </Routes>
    </MemoryRouter>,
  );
  return api;
}

function versionRows() {
  const table = within(screen.getByRole('region', { name: 'Версии' })).getByRole('table');
  return within(table)
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell').map((c) => c.textContent));
}

describe('InstrumentPage', () => {
  it('shows the instrument, its alerts and versions, newest first', async () => {
    const api = fakeApi();
    api.instrument.mockResolvedValue(EDITED);
    renderCard(api);

    expect(await screen.findByRole('heading', { level: 2 })).toHaveTextContent(
      'SU26238RMFS4 — ОФЗ-ПД 26238',
    );
    expect(screen.getByText('ОФЗ-ПД 26238 15/05/2041 · RU000A1038V6 · облигация')).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Предупреждения' })).toHaveTextContent(
      'закреплено вручную и расходится с ISS',
    );
    expect(versionRows()).toEqual([
      [
        '2',
        '01.10.2026',
        'вручнуюops, 21.10.2026, 12:00',
        'Краткое название по проспектуЗакреплено: Краткое название',
      ],
      ['1', 'с начала обращения', 'MOEX ISStrader, 20.10.2026, 10:15', 'Загружена из MOEX ISS'],
    ]);
    expect(screen.getByRole('row', { name: /^2 01\.10\.2026/ })).toHaveClass('selected');
    expect(await screen.findByRole('region', { name: 'Версия 2' })).toBeInTheDocument();
    expect(api.version).not.toHaveBeenCalled();
  });

  it('shows an older version on request and returns to the latest without asking the server', async () => {
    const api = fakeApi();
    api.instrument.mockResolvedValue(EDITED);
    api.version.mockResolvedValue(version(1));
    renderCard(api);
    await screen.findByRole('region', { name: 'Версия 2' });

    await userEvent.click(screen.getByRole('button', { name: 'Показать версию 1' }));

    expect(api.version).toHaveBeenCalledWith('i-1', 1);
    expect(await screen.findByRole('heading', { name: 'Версия 1: с начала обращения' })).toBeInTheDocument();
    expect(screen.getByRole('row', { name: /^1 с начала обращения/ })).toHaveClass('selected');

    await userEvent.click(screen.getByRole('button', { name: 'Показать версию 2' }));

    expect(await screen.findByRole('heading', { name: 'Версия 2: действует с 01.10.2026' })).toBeInTheDocument();
    expect(api.version).toHaveBeenCalledOnce();
  });

  it('tells who can edit and hides the ISS refresh from others', async () => {
    const api = fakeApi();
    api.instrument.mockResolvedValue(instrument([version(1, { comment: null })]));
    renderCard(api);

    expect(await screen.findByText(/Править бумагу может сопровождение/)).toBeInTheDocument();
    expect(versionRows()[0]?.[3]).toBe('—');
    expect(screen.queryByRole('button', { name: 'Обновить из ISS' })).not.toBeInTheDocument();
    expect(screen.queryByRole('form', { name: 'Правка' })).not.toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Предупреждения' })).not.toBeInTheDocument();
  });

  it('gives operations the edit form', async () => {
    renderCard(fakeApi(), true);

    expect(await screen.findByRole('form', { name: 'Правка' })).toBeInTheDocument();
  });

  it('shows when the last check against ISS failed', async () => {
    const api = fakeApi();
    api.instrument.mockResolvedValue(instrument(undefined, { last_sync_error: 'ISS недоступен' }));
    renderCard(api);

    expect(await screen.findByText(/попытка сверки не удалась: ISS недоступен/)).toBeInTheDocument();
    expect(screen.getByText(/Последняя сверка с MOEX ISS: 26\.10\.2026, 06:00/)).toBeInTheDocument();
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

    expect(await screen.findByRole('region', { name: 'Версия 2' })).toBeInTheDocument();
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

  it('shows the issuer, ratings and classifiers of the bond', async () => {
    const api = renderCard();

    expect(await screen.findByText(/тип в ISS: ОФЗ/)).toBeInTheDocument();
    expect(await screen.findByRole('table', { name: 'Действующие рейтинги' })).toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'Значения сейчас' })).toBeInTheDocument();
    expect(api.ratings).toHaveBeenCalledWith({ instrument_id: 'i-1' });
    // Вести рейтинги может только риск-менеджмент.
    expect(screen.queryByRole('form', { name: 'Новая запись о рейтинге' })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('link', { name: 'Министерство финансов Российской Федерации' }));
    expect(screen.getByText('Карточка эмитента')).toBeInTheDocument();
  });

  it('gives risk management the rating and classifier forms', async () => {
    renderCard(fakeApi(), false, true);

    expect(await screen.findByRole('form', { name: 'Новая запись о рейтинге' })).toBeInTheDocument();
    expect(await screen.findByRole('form', { name: 'Новое значение классификатора' })).toBeInTheDocument();
  });

  it('says when the issuer is not known yet and has no ratings for a fund share', async () => {
    const api = fakeApi();
    api.instrument.mockResolvedValue(
      instrument([version(1)], { kind: 'fund_share', issuer: null, iss_type: null }),
    );
    renderCard(api);

    expect(await screen.findByText('появится после сверки с MOEX ISS')).toBeInTheDocument();
    expect(screen.getByText(/тип в ISS: —/)).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Рейтинги' })).not.toBeInTheDocument();
    expect(api.ratings).not.toHaveBeenCalled();
  });

  it('leads back to the list', async () => {
    renderCard();

    await userEvent.click(screen.getByRole('link', { name: '← Все бумаги' }));

    expect(screen.getByText('Список бумаг')).toBeInTheDocument();
  });
});
