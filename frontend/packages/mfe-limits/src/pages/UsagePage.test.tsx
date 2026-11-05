import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { check, fakeApi, fundDay, renderLimits } from '../test/fixtures';

function rows() {
  return within(screen.getByRole('table', { name: 'Лимиты' }))
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell').map((c) => c.textContent));
}

describe('UsagePage', () => {
  it('shows the fund day: figures and every check', async () => {
    const api = renderLimits();

    expect(await screen.findByText(/DOHOD2, 10\.11\.2026, набор — версия 1 с 01\.11\.2026\. Нарушений нет\./)).toBeInTheDocument();
    expect(api.day).toHaveBeenCalledWith('f-dohod', '2026-11-17');
    const figures = screen.getByLabelText('Фонд за день');
    expect(figures).toHaveTextContent(
      'Активы2 800 000 000,00 ₽Обязательства1 800 000 000,00 ₽СЧА1 000 000 000,00 ₽Плечо2,80Дюрация к активам0,43 года',
    );
    expect(rows()).toEqual([
      ['Л1 · Плечоп. 5.1', '2,80', 'не более 4,0', '70%', 'в пределах'],
      ['Л3 · Корпоративные облигации AAA и AA+ · на эмитентап. 6.1', '9,64%, Эмитент А', 'не более 10%', '96%', 'близко к лимитуПодробнее'],
      ['4 · Допустимые инструменты', 'нарушений нет', '—', '—', 'в пределах'],
    ]);
  });

  it('opens and closes the details of a check', async () => {
    const api = fakeApi();
    api.day.mockResolvedValue(
      fundDay({
        checks: [
          check({
            status: 'breach',
            groups: [{ key: 'e-g', name: 'Эмитент Г', value: '2.6786', status: 'breach' }],
            violations: [{ secid: 'X', reason: 'позиция не подходит ни под одно правило' }],
            missing: [{ secid: 'Y', reason: 'не задано «Очерёдность»' }],
          }),
        ],
      }),
    );
    renderLimits(api);

    expect(await screen.findByText(/Нарушено проверок: 1\./)).toHaveClass('notice-error');
    await userEvent.click(screen.getByRole('button', { name: 'Подробнее' }));
    const details = screen.getByRole('group', { name: 'Подробно: Л3' });
    expect(within(details).getByRole('list', { name: 'Группы' })).toHaveTextContent('Эмитент Г: 2,68% — нарушение');
    expect(within(details).getByRole('list', { name: 'Нарушения' })).toHaveTextContent(
      'X — позиция не подходит ни под одно правило',
    );
    expect(within(details).getByRole('list', { name: 'Нет данных' })).toHaveTextContent('Y — не задано «Очерёдность»');

    await userEvent.click(screen.getByRole('button', { name: 'Скрыть' }));
    expect(screen.queryByRole('group', { name: 'Подробно: Л3' })).not.toBeInTheDocument();
  });

  it('switches the fund and the day; today is by current prices', async () => {
    const api = fakeApi();
    api.day.mockResolvedValue(fundDay({ today: true }));
    renderLimits(api);

    expect(await screen.findByText(/Сегодня — по текущим ценам/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText('Фонд'), 'f-opif');
    expect(api.day).toHaveBeenLastCalledWith('f-opif', '2026-11-17');
    fireEvent.change(screen.getByLabelText('День'), { target: { value: '2026-11-12' } });
    expect(api.day).toHaveBeenLastCalledWith('f-opif', '2026-11-12');
    // Очистили дату — снова сегодня.
    fireEvent.change(screen.getByLabelText('День'), { target: { value: '' } });
    expect(api.day).toHaveBeenLastCalledWith('f-opif', '2026-11-17');
  });

  it('tells about open and overdue breaches in the journal', async () => {
    const api = fakeApi();
    api.day.mockResolvedValueOnce(fundDay({ open_breaches: 2, overdue_breaches: 1 }));
    renderLimits(api);

    expect(await screen.findByRole('status')).toHaveTextContent(
      'DOHOD2: открытых нарушений в журнале — 2, из них просрочено — 1. Журнал нарушений',
    );
    api.day.mockResolvedValueOnce(fundDay({ open_breaches: 1 }));
    fireEvent.change(screen.getByLabelText('День'), { target: { value: '2026-11-12' } });
    expect(await screen.findByText(/открытых нарушений в журнале — 1\./)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('link', { name: 'Журнал нарушений' }));
    expect(await screen.findByRole('table', { name: 'Журнал нарушений' })).toBeInTheDocument();
  });

  it('says when there is no set or no calculation for the day', async () => {
    const api = fakeApi();
    api.day.mockResolvedValueOnce(fundDay({ limit_set: null, calculated: false, totals: null, checks: [] }));
    renderLimits(api);
    expect(await screen.findByText(/набора лимитов на этот день нет/)).toBeInTheDocument();

    api.day.mockResolvedValueOnce(fundDay({ calculated: false, totals: null, checks: [] }));
    fireEvent.change(screen.getByLabelText('День'), { target: { value: '2026-11-18' } });
    expect(await screen.findByText(/Расчёта фонда за этот день ещё нет/)).toBeInTheDocument();
  });
});
