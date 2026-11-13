import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import { describe, expect, it } from 'vitest';

import { asApi, fakeApi, type FakeApi } from '../test/fixtures';
import { IssuersPage } from './IssuersPage';

function OpenedIssuer() {
  return <p>Открыт эмитент {useParams().id}</p>;
}

function renderPage(api: FakeApi = fakeApi()) {
  render(
    <MemoryRouter initialEntries={['/refdata/issuers']}>
      <Routes>
        <Route path="/refdata/issuers" element={<IssuersPage api={asApi(api)} basePath="/refdata" />} />
        <Route path="/refdata/issuers/:id" element={<OpenedIssuer />} />
      </Routes>
    </MemoryRouter>,
  );
  return api;
}

describe('IssuersPage', () => {
  it('lists issuers with their INN, ISS code and number of bonds', async () => {
    renderPage();

    const table = await screen.findByRole('table');
    const rows = within(table)
      .getAllByRole('row')
      .slice(1)
      .map((row) => within(row).getAllByRole('cell').map((c) => c.textContent));
    expect(rows).toEqual([
      // Организационная форма — коротко; ИНН на телефоне — второй строкой.
      ['Минфин РоссииИНН 7710168360', '7710168360', '1228', '2'],
      ['ГК «ВЭБ.РФ»ИНН —', '—', '406', '1'],
    ]);

    await userEvent.click(screen.getByRole('link', { name: 'ГК «ВЭБ.РФ»' }));
    expect(screen.getByText('Открыт эмитент e-2')).toBeInTheDocument();
  });

  it('searches by name or INN and says when nothing is found', async () => {
    const api = fakeApi();
    api.issuers.mockResolvedValue([]);
    renderPage(api);

    expect(await screen.findByText(/Эмитентов пока нет/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('Поиск эмитента'), 'ВЭБ ');

    expect(await screen.findByText('Ничего не нашлось.')).toBeInTheDocument();
    expect(api.issuers).toHaveBeenLastCalledWith('ВЭБ');
  });
});
