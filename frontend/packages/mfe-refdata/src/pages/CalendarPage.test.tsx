import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { CalendarDay } from '../api';
import { asApi, fakeApi, ruleViolation, type FakeApi } from '../test/fixtures';
import { CalendarPage } from './CalendarPage';

const TODAY = '2026-10-26';

function renderCalendar(api: FakeApi = fakeApi(), canEdit = true, today = TODAY) {
  render(<CalendarPage api={asApi(api)} canEdit={canEdit} today={today} />);
  return api;
}

function stats() {
  const list = within(screen.getByLabelText(/^Сводка/));
  const terms = list.getAllByRole('term').map((dt) => dt.textContent);
  const values = list.getAllByRole('definition').map((dd) => dd.textContent);
  return Object.fromEntries(terms.map((term, i) => [term, values[i]]));
}

function exceptions() {
  return within(screen.getByRole('region', { name: /^Исключения/ }))
    .getAllByRole('listitem')
    .map((li) => li.textContent);
}

async function openDay(name: string) {
  await userEvent.click(await screen.findByRole('button', { name }));
  return within(screen.getByRole('dialog')).getByRole('form', { name: 'Исключение календаря' });
}

describe('CalendarPage: the year', () => {
  it('shows the year as twelve months with holidays, working weekends and today', async () => {
    const api = renderCalendar(fakeApi(), false);

    expect(await screen.findByRole('heading', { name: '2026' })).toBeInTheDocument();
    expect(api.calendar).toHaveBeenCalledWith(2026);
    expect(stats()).toEqual({ 'Торговых дней': '261', 'Праздников в будни': '1', 'Рабочих выходных': '1' });
    expect(screen.getAllByRole('table')).toHaveLength(12);

    const november = screen.getByRole('table', { name: 'Ноябрь 2026' });
    const holiday = within(november).getByText('4');
    expect(holiday).toHaveClass('cal-day', 'holiday');
    expect(holiday).toHaveAttribute('title', '4 ноября, среда — праздник, торгов нет: День народного единства');
    expect(within(november).getByText('7')).toHaveClass('weekend');
    expect(within(november).getByText('5')).not.toHaveAttribute('title');
    expect(november.closest('.cal-month')).toHaveTextContent(/^Ноябрь20 торговых дней/);
    const december = screen.getByRole('table', { name: 'Декабрь 2026' });
    expect(within(december).getByText('27')).toHaveAttribute('title', '27 декабря, воскресенье — рабочий выходной, торги есть');
    expect(within(screen.getByRole('table', { name: 'Октябрь 2026' })).getByText('26')).toHaveClass('regular', 'today');

    expect(exceptions()).toEqual(['4 ноя, срДень народного единствапраздник', '27 дек, всрабочий выходной']);
    // Смотрят все, дни меняет сопровождение.
    expect(screen.queryByRole('button', { name: /^5 ноября/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^4 ноя/ })).not.toBeInTheDocument();
    expect(screen.getByText(/календарю Московской биржи\.$/)).toBeInTheDocument();
  });

  it('switches years and leads back to the current one', async () => {
    // Год — 365 клеток; щелчки без userEvent, чтобы тест не тянулся.
    const api = renderCalendar(fakeApi(), false);
    await screen.findByRole('heading', { name: '2026' });
    expect(screen.queryByRole('button', { name: /^к 2026 году/ })).not.toBeInTheDocument();
    api.calendar.mockResolvedValue([]);

    fireEvent.click(screen.getByRole('button', { name: 'Следующий год' }));
    expect(await screen.findByText(/Исключений за 2027 год нет/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '2027' })).toBeInTheDocument();
    expect(api.calendar).toHaveBeenLastCalledWith(2027);

    fireEvent.click(screen.getByRole('button', { name: 'к 2026 году' }));
    fireEvent.click(screen.getByRole('button', { name: 'Предыдущий год' }));
    expect(await screen.findByText(/Исключений за 2025 год нет/)).toBeInTheDocument();
  });

  it.each([
    ['2000-03-01', 'Предыдущий год'],
    ['2100-03-01', 'Следующий год'],
  ])('stops at the edge of the supported years: %s', async (today, button) => {
    renderCalendar(fakeApi(), false, today);

    expect(screen.getByRole('button', { name: button })).toBeDisabled();
  });
});

describe('CalendarPage: a day', () => {
  it('edits a holiday: the panel shows it now and keeps the comment', async () => {
    const api = renderCalendar();
    const form = await openDay('4 ноября, среда — праздник, торгов нет: День народного единства');

    expect(screen.getByRole('dialog', { name: '4 ноября 2026, среда' })).toBeInTheDocument();
    expect(within(form).getByText('Сейчас: праздник, торгов нет · День народного единства')).toBeInTheDocument();
    expect(within(form).getByRole('radio', { name: /^Праздник — торгов нет/ })).toBeChecked();
    expect(within(form).getByLabelText('Комментарий')).toHaveValue('День народного единства');
    await userEvent.clear(within(form).getByLabelText('Комментарий'));
    await userEvent.type(within(form).getByLabelText('Комментарий'), ' День единства ');
    await userEvent.click(within(form).getByRole('button', { name: 'Сохранить' }));

    expect(api.setDay).toHaveBeenCalledWith('2026-11-04', { is_trading: false, comment: 'День единства' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('offers a holiday for a weekday and says a plain day needs no exception', async () => {
    const api = renderCalendar();
    const form = await openDay('5 ноября, четверг — торговый день');

    expect(within(form).getByText('Сейчас: торговый день')).toBeInTheDocument();
    expect(within(form).getByRole('radio', { name: /^Праздник — торгов нет/ })).toBeChecked();
    await userEvent.click(within(form).getByRole('radio', { name: /^Обычный торговый день/ }));
    expect(within(form).getByText('Исключение не нужно: день и так торговый.')).toBeInTheDocument();
    expect(within(form).queryByRole('button')).not.toBeInTheDocument();
    expect(within(form).queryByLabelText('Комментарий')).not.toBeInTheDocument();

    await userEvent.click(within(form).getByRole('radio', { name: /^Праздник — торгов нет/ }));
    await userEvent.click(within(form).getByRole('button', { name: 'Сохранить' }));
    expect(api.setDay).toHaveBeenCalledWith('2026-11-05', { is_trading: false, comment: null });
  });

  it('offers a working day for a weekend', async () => {
    const api = renderCalendar();
    const form = await openDay('7 ноября, суббота — выходной');

    await userEvent.click(within(form).getByRole('radio', { name: /^Обычный выходной/ }));
    expect(within(form).getByText('Исключение не нужно: день и так выходной.')).toBeInTheDocument();
    await userEvent.click(within(form).getByRole('radio', { name: /^Рабочий выходной — торги есть/ }));
    expect(within(form).getByLabelText('Комментарий')).toHaveAttribute('placeholder', 'Перенос рабочего дня');
    await userEvent.type(within(form).getByLabelText('Комментарий'), 'Перенос с 4 ноября');
    await userEvent.click(within(form).getByRole('button', { name: 'Сохранить' }));

    expect(api.setDay).toHaveBeenCalledWith('2026-11-07', { is_trading: true, comment: 'Перенос с 4 ноября' });
  });

  it('removes an exception opened from the list', async () => {
    const api = renderCalendar();
    await userEvent.click(await screen.findByRole('button', { name: /^27 дек, вс/ }));
    const form = within(screen.getByRole('dialog', { name: '27 декабря 2026, воскресенье' })).getByRole('form');

    await userEvent.click(within(form).getByRole('radio', { name: /^Обычный выходной/ }));
    await userEvent.click(within(form).getByRole('button', { name: 'Убрать исключение' }));

    expect(api.clearDay).toHaveBeenCalledWith('2026-12-27');
    expect(api.calendar).toHaveBeenCalledWith(2026);
  });

  it('suggests removing an exception that changes nothing', async () => {
    const api = fakeApi();
    api.calendar.mockResolvedValue([{ day: '2026-11-07', is_trading: false, comment: 'Ошибка' }]);
    renderCalendar(api);
    const form = await openDay('7 ноября, суббота — праздник, торгов нет: Ошибка');

    expect(within(form).getByRole('radio', { name: /^Обычный выходной/ })).toBeChecked();
    await userEvent.click(within(form).getByRole('radio', { name: /^Рабочий выходной/ }));
    // Комментарий ошибочной записи в новую не переносим.
    expect(within(form).getByLabelText('Комментарий')).toHaveValue('');
  });

  it('keeps the panel when the service refuses, and closes it on request', async () => {
    const api = fakeApi();
    api.setDay.mockRejectedValue(ruleViolation('Неверная дата.'));
    renderCalendar(api);
    const form = await openDay('5 ноября, четверг — торговый день');

    await userEvent.type(within(form).getByLabelText('Комментарий'), 'Праздник');
    await userEvent.click(within(form).getByRole('button', { name: 'Сохранить' }));

    expect(await within(form).findByRole('alert')).toHaveTextContent('Неверная дата.');
    expect(within(form).getByLabelText('Комментарий')).toHaveValue('Праздник');
    await userEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('CalendarPage: trading days ahead', () => {
  function calculator() {
    return within(screen.getByRole('form', { name: 'Калькулятор торговых дней' }));
  }

  it('counts at once, from today, and by the quick steps', async () => {
    const api = renderCalendar(fakeApi(), false);

    expect(await calculator().findByRole('status')).toHaveTextContent('Т+1 →27 октября, вторник');
    expect(api.addTradingDays).toHaveBeenCalledWith(TODAY, 1);
    expect(calculator().getByRole('button', { name: 'Т+1' })).toHaveAttribute('aria-pressed', 'true');

    await userEvent.click(calculator().getByRole('button', { name: 'Т+5' }));
    expect(calculator().getByLabelText('Торговых дней')).toHaveValue(5);
    await userEvent.clear(calculator().getByLabelText('Торговых дней'));
    await userEvent.type(calculator().getByLabelText('Торговых дней'), '2');
    fireEvent.change(calculator().getByLabelText('От даты'), { target: { value: '2026-10-30' } });

    expect(api.addTradingDays).toHaveBeenLastCalledWith('2026-10-30', 2);
    expect(await calculator().findByText('Т+2 →')).toBeInTheDocument();
    // Enter в поле ничего не отправляет: ответ уже на экране.
    expect(fireEvent.submit(screen.getByRole('form', { name: 'Калькулятор торговых дней' }))).toBe(false);
    expect(calculator().getByRole('status')).toBeInTheDocument();
  });

  it('names the holidays it skipped and the year when it crosses one', async () => {
    const api = fakeApi();
    api.addTradingDays.mockResolvedValue({ date: '2026-11-05' });
    api.calendar.mockImplementation((year: unknown) =>
      Promise.resolve<CalendarDay[]>(
        year === 2027
          ? [{ day: '2027-01-08', is_trading: false, comment: null }]
          : [{ day: '2026-11-04', is_trading: false, comment: 'День народного единства' }],
      ),
    );
    renderCalendar(api, false, '2026-11-03');

    expect(await calculator().findByText('пропущены праздники: 04.11 — День народного единства')).toBeInTheDocument();

    api.addTradingDays.mockResolvedValue({ date: '2027-01-11' });
    fireEvent.change(calculator().getByLabelText('От даты'), { target: { value: '2026-12-30' } });
    expect(await calculator().findByText('11 января 2027, понедельник')).toBeInTheDocument();
    expect(calculator().getByText('пропущены праздники: 08.01')).toBeInTheDocument();
    expect(api.calendar).toHaveBeenCalledWith(2027);
  });

  it('asks for a date and a number from 0 to 30', async () => {
    const api = renderCalendar(fakeApi(), false);
    await calculator().findByRole('status');

    await userEvent.clear(calculator().getByLabelText('Торговых дней'));
    await userEvent.type(calculator().getByLabelText('Торговых дней'), '31');
    expect(calculator().getByText('Укажите дату и число торговых дней от 0 до 30.')).toBeInTheDocument();
    expect(calculator().queryByRole('status')).not.toBeInTheDocument();

    await userEvent.clear(calculator().getByLabelText('Торговых дней'));
    await userEvent.type(calculator().getByLabelText('Торговых дней'), '0');
    fireEvent.change(calculator().getByLabelText('От даты'), { target: { value: '' } });
    expect(calculator().getByText(/Укажите дату/)).toBeInTheDocument();
    expect(api.addTradingDays).not.toHaveBeenCalledWith('', 0);
  });

  it('says why counting failed', async () => {
    const api = fakeApi();
    api.addTradingDays.mockRejectedValue(ruleViolation('Не больше 30 дней.'));
    renderCalendar(api, false);

    expect(await calculator().findByRole('alert')).toHaveTextContent('Не больше 30 дней.');
  });

  it('uses today in Moscow by default', () => {
    render(<CalendarPage api={asApi(fakeApi())} canEdit={false} />);

    expect(screen.getByLabelText('От даты')).toHaveValue(
      new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/Moscow' }),
    );
  });
});
