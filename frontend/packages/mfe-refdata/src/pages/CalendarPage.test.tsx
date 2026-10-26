import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { asApi, fakeApi, ruleViolation, type FakeApi } from '../test/fixtures';
import { CalendarPage } from './CalendarPage';

const TODAY = '2026-10-26';

function renderCalendar(api: FakeApi = fakeApi(), canEdit = true, today = TODAY) {
  render(<CalendarPage api={asApi(api)} canEdit={canEdit} today={today} />);
  return api;
}

function rows() {
  return within(screen.getByRole('region', { name: 'Исключения календаря' }))
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell').map((c) => c.textContent));
}

describe('CalendarPage', () => {
  it('lists the exceptions of the current year', async () => {
    const api = renderCalendar(fakeApi(), false);

    expect(await screen.findByRole('heading', { name: /2026/ })).toBeInTheDocument();
    await screen.findByRole('table');
    expect(rows()).toEqual([
      ['04.11.2026, ср', 'праздник — торгов нет', 'День народного единства'],
      ['27.12.2026, вс', 'рабочий выходной — торги есть', '—'],
    ]);
    expect(api.calendar).toHaveBeenCalledWith(2026);
    expect(screen.queryByRole('form', { name: 'Исключение календаря' })).not.toBeInTheDocument();
  });

  it('switches years', async () => {
    const api = renderCalendar();
    await screen.findByRole('table');
    api.calendar.mockResolvedValue([]);

    await userEvent.click(screen.getByRole('button', { name: 'Следующий год' }));
    expect(await screen.findByText(/Исключений за 2027 год нет/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Предыдущий год' }));
    await userEvent.click(screen.getByRole('button', { name: 'Предыдущий год' }));
    expect(await screen.findByText(/Исключений за 2025 год нет/)).toBeInTheDocument();
    expect(api.calendar.mock.calls.map(([year]) => year)).toEqual([2026, 2027, 2026, 2025]);
  });

  it.each([
    ['2000-03-01', 'Предыдущий год'],
    ['2100-03-01', 'Следующий год'],
  ])('stops at the edge of the supported years: %s', async (today, button) => {
    renderCalendar(fakeApi(), false, today);

    expect(screen.getByRole('button', { name: button })).toBeDisabled();
  });

  it('removes an exception', async () => {
    const api = renderCalendar();
    await screen.findByRole('table');

    await userEvent.click(screen.getByRole('button', { name: 'Удалить исключение 04.11.2026' }));

    expect(api.clearDay).toHaveBeenCalledWith('2026-11-04');
    expect(api.calendar).toHaveBeenCalledTimes(2);
  });

  it('says why removing failed', async () => {
    const api = fakeApi();
    api.clearDay.mockRejectedValue(ruleViolation('Исключения на эту дату нет.'));
    renderCalendar(api);
    await screen.findByRole('table');

    await userEvent.click(screen.getByRole('button', { name: 'Удалить исключение 04.11.2026' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Исключения на эту дату нет.');
  });

  it('adds a working weekend in another year and shows that year', async () => {
    const api = renderCalendar();
    const form = within(screen.getByRole('form', { name: 'Исключение календаря' }));
    expect(form.getByLabelText('Дата')).toHaveValue(TODAY);

    fireEvent.change(form.getByLabelText('Дата'), { target: { value: '2027-01-09' } });
    await userEvent.selectOptions(form.getByLabelText('Что в этот день'), 'trading');
    await userEvent.type(form.getByLabelText('Комментарий'), ' Перенос выходного ');
    await userEvent.click(form.getByRole('button', { name: 'Сохранить' }));

    expect(api.setDay).toHaveBeenCalledWith('2027-01-09', {
      is_trading: true,
      comment: 'Перенос выходного',
    });
    expect(await screen.findByRole('heading', { name: /2027/ })).toBeInTheDocument();
    expect(api.calendar).toHaveBeenLastCalledWith(2027);
    expect(form.getByLabelText('Комментарий')).toHaveValue('');
  });

  it('adds a holiday without a comment', async () => {
    const api = renderCalendar();
    const form = within(screen.getByRole('form', { name: 'Исключение календаря' }));

    fireEvent.change(form.getByLabelText('Дата'), { target: { value: '2026-12-31' } });
    await userEvent.click(form.getByRole('button', { name: 'Сохранить' }));

    expect(api.setDay).toHaveBeenCalledWith('2026-12-31', { is_trading: false, comment: null });
  });

  it('keeps the form filled in when saving failed', async () => {
    const api = fakeApi();
    api.setDay.mockRejectedValue(ruleViolation('Неверная дата.'));
    renderCalendar(api);
    const form = within(screen.getByRole('form', { name: 'Исключение календаря' }));

    await userEvent.type(form.getByLabelText('Комментарий'), 'Праздник');
    await userEvent.click(form.getByRole('button', { name: 'Сохранить' }));

    expect(await form.findByRole('alert')).toHaveTextContent('Неверная дата.');
    expect(form.getByLabelText('Комментарий')).toHaveValue('Праздник');
  });
});

describe('CalendarPage: trading days ahead', () => {
  it('counts trading days from a date', async () => {
    const api = renderCalendar(fakeApi(), false);
    const form = within(screen.getByRole('form', { name: 'Дата через N торговых дней' }));

    fireEvent.change(form.getByLabelText('От даты'), { target: { value: '2026-10-30' } });
    await userEvent.clear(form.getByLabelText('Торговых дней'));
    await userEvent.type(form.getByLabelText('Торговых дней'), '2');
    await userEvent.click(form.getByRole('button', { name: 'Посчитать' }));

    expect(api.addTradingDays).toHaveBeenCalledWith('2026-10-30', 2);
    expect(await form.findByRole('status')).toHaveTextContent('27.10.2026, вт');
  });

  it('hides an old answer once the question changes', async () => {
    renderCalendar(fakeApi(), false);
    const form = within(screen.getByRole('form', { name: 'Дата через N торговых дней' }));

    await userEvent.click(form.getByRole('button', { name: 'Посчитать' }));
    expect(await form.findByRole('status')).toBeInTheDocument();
    await userEvent.type(form.getByLabelText('Торговых дней'), '0');
    expect(form.queryByRole('status')).not.toBeInTheDocument();

    await userEvent.click(form.getByRole('button', { name: 'Посчитать' }));
    expect(await form.findByRole('status')).toBeInTheDocument();
    fireEvent.change(form.getByLabelText('От даты'), { target: { value: '2026-11-02' } });
    expect(form.queryByRole('status')).not.toBeInTheDocument();
  });

  it('says why counting failed', async () => {
    const api = fakeApi();
    api.addTradingDays.mockRejectedValue(ruleViolation('Не больше 30 дней.'));
    renderCalendar(api, false);
    const form = within(screen.getByRole('form', { name: 'Дата через N торговых дней' }));

    await userEvent.click(form.getByRole('button', { name: 'Посчитать' }));

    expect(await form.findByRole('alert')).toHaveTextContent('Не больше 30 дней.');
  });

  it('uses today in Moscow by default', () => {
    render(<CalendarPage api={asApi(fakeApi())} canEdit={false} />);

    expect(screen.getByLabelText('От даты')).toHaveValue(
      new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/Moscow' }),
    );
  });
});
