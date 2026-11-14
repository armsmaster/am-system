import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it } from 'vitest';

import { asApi, fakeApi, ISSUER, type FakeApi } from '../test/fixtures';
import { IssuerPage } from './IssuerPage';

function renderPage(api: FakeApi = fakeApi(), canRate = false) {
  render(
    <MemoryRouter initialEntries={['/refdata/issuers/e-1']}>
      <Routes>
        <Route
          path="/refdata/issuers/e-1"
          element={<IssuerPage api={asApi(api)} id="e-1" basePath="/refdata" canRate={canRate} />}
        />
        <Route path="/refdata/issuers" element={<p>Все эмитенты</p>} />
        <Route path="/refdata/instruments/:id" element={<p>Карточка бумаги</p>} />
      </Routes>
    </MemoryRouter>,
  );
  return api;
}

describe('IssuerPage', () => {
  it('shows the issuer, its bonds, ratings and classifiers', async () => {
    const api = renderPage();

    expect(await screen.findByRole('heading', { level: 2 })).toHaveTextContent(
      'Министерство финансов Российской Федерации',
    );
    expect(screen.getByText('ИНН 7710168360 · код эмитента в MOEX ISS 1228')).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Выпуски' })).getAllByRole('listitem').map((i) => i.textContent)).toEqual(
      ['ОФЗ 26238SU26238RMFS4фикс 7,1% · погашение 15.05.2041 · оферта 17.05.2028', 'LQDTLQDTпай фонда'],
    );
    expect(await screen.findByRole('list', { name: 'Действующие рейтинги' })).toBeInTheDocument();
    expect(api.ratings).toHaveBeenCalledWith({ issuer_id: 'e-1' });
    // У эмитента — классификаторы эмитентов.
    expect(await screen.findByLabelText('Значения сейчас')).toHaveTextContent('Тип эмитента');
    expect(screen.queryByRole('form')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('link', { name: 'ОФЗ 26238' }));
    expect(screen.getByText('Карточка бумаги')).toBeInTheDocument();
  });

  it('gives risk management the forms and leads back to the list', async () => {
    const api = fakeApi();
    api.issuer.mockResolvedValue({ ...ISSUER, inn: null });
    renderPage(api, true);

    expect(await screen.findByText(/^ИНН — ·/)).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Добавить запись' })).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Задать значение' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('link', { name: '← Все эмитенты' }));
    expect(screen.getByText('Все эмитенты')).toBeInTheDocument();
  });
});
