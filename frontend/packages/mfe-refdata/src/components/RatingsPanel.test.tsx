import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { asApi, fakeApi, RATINGS, ruleViolation, type FakeApi } from '../test/fixtures';
import { RatingsPanel } from './RatingsPanel';

const ISSUER = { issuer_id: 'e-1' } as const;

function renderPanel(api: FakeApi = fakeApi(), canEdit = false) {
  render(<RatingsPanel api={asApi(api)} subject={ISSUER} canEdit={canEdit} today="2026-11-17" />);
  return api;
}

function rows(name: string) {
  return within(screen.getByRole('table', { name }))
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell').map((c) => c.textContent));
}

/** Открывает выдвижную панель с формой новой записи и отдаёт форму. */
async function openForm() {
  await userEvent.click(await screen.findByRole('button', { name: 'Добавить запись' }));
  return within(screen.getByRole('dialog', { name: 'Новая запись о рейтинге' })).findByRole('form', {
    name: 'Новая запись о рейтинге',
  });
}

describe('RatingsPanel', () => {
  it('shows ratings in force and the history, newest first', async () => {
    const api = renderPanel();

    const now = await screen.findByRole('list', { name: 'Действующие рейтинги' });
    expect(api.ratings).toHaveBeenCalledWith(ISSUER);
    expect(within(now).getAllByRole('listitem').map((i) => i.textContent)).toEqual([
      'АКРАAA-(RU)общая шкала AA-с 11.11.2026',
    ]);
    expect(rows('История рейтингов')).toEqual([
      ['11.11.2026', 'АКРА AA-(RU) · с 11.11.2026risk, 02.11.2026, 12:00 · Пресс-релиз АКРА'],
      [
        '10.11.2026',
        'НКР A+.ru · с 10.11.2026risk, 02.11.2026, 12:00Отменена: Не тот эмитент (risk, 10.11.2026, 15:00)',
      ],
      ['01.06.2026', 'АКРА AA+(RU) · с 01.06.2026risk, 02.11.2026, 12:00'],
    ]);
    // Отменённая запись остаётся в истории, но приглушена.
    expect(screen.getByText('НКР A+.ru').closest('tr')).toHaveClass('cancelled');
    // Смотрят все, записывает риск-менеджмент.
    expect(screen.queryByRole('button', { name: 'Добавить запись' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Отменить/ })).not.toBeInTheDocument();
  });

  it('tells "not entered yet" from "no rating"', async () => {
    const api = fakeApi();
    api.ratings.mockResolvedValueOnce({ now: { status: 'unknown', ratings: [] }, records: [] });
    const { unmount } = render(<RatingsPanel api={asApi(api)} subject={ISSUER} canEdit={false} />);

    expect(await screen.findByText(/Рейтинг ещё не вводили/)).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.queryByText('История записей')).not.toBeInTheDocument();
    unmount();

    api.ratings.mockResolvedValueOnce({ now: { status: 'unrated', ratings: [] }, records: RATINGS.records });
    render(<RatingsPanel api={asApi(api)} subject={ISSUER} canEdit={false} />);
    expect(await screen.findByText(/Рейтинга нет: агентства/)).toBeInTheDocument();
  });

  it('records a new rating of the chosen agency and closes the panel', async () => {
    const api = renderPanel(fakeApi(), true);
    const form = await openForm();

    expect(within(form).getByLabelText('С даты')).toHaveValue('2026-11-17');
    await userEvent.selectOptions(within(form).getByLabelText('Рейтинг'), 'AA-(RU)');
    // Сменили агентство — та же ступень в его записи.
    await userEvent.selectOptions(within(form).getByLabelText('Агентство'), 'expert_ra');
    expect(within(form).getByLabelText('Рейтинг')).toHaveValue('ruAA-');
    await userEvent.clear(within(form).getByLabelText('С даты'));
    await userEvent.type(within(form).getByLabelText('С даты'), '2026-11-12');
    await userEvent.type(within(form).getByLabelText('Комментарий'), ' Пресс-релиз ');
    await userEvent.click(within(form).getByRole('button', { name: 'Записать' }));

    expect(api.addRating).toHaveBeenCalledWith(ISSUER, {
      action: 'assigned',
      agency: 'expert_ra',
      rating: 'ruAA-',
      valid_from: '2026-11-12',
      comment: 'Пресс-релиз',
    });
    expect(api.ratings).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('records a withdrawal and "no rating" without the fields they do not need', async () => {
    const api = renderPanel(fakeApi(), true);
    let form = await openForm();

    await userEvent.selectOptions(within(form).getByLabelText('Что произошло'), 'withdrawn');
    expect(within(form).queryByLabelText('Рейтинг')).not.toBeInTheDocument();
    await userEvent.click(within(form).getByRole('button', { name: 'Записать' }));
    expect(api.addRating).toHaveBeenLastCalledWith(ISSUER, {
      action: 'withdrawn',
      agency: 'acra',
      rating: null,
      valid_from: '2026-11-17',
      comment: null,
    });

    form = await openForm();
    await userEvent.selectOptions(within(form).getByLabelText('Что произошло'), 'unrated');
    expect(within(form).queryByLabelText('Агентство')).not.toBeInTheDocument();
    await userEvent.click(within(form).getByRole('button', { name: 'Записать' }));
    expect(api.addRating).toHaveBeenLastCalledWith(ISSUER, expect.objectContaining({ action: 'unrated', agency: null }));
  });

  it('keeps the panel and the comment when the service refuses', async () => {
    const api = fakeApi();
    api.addRating.mockRejectedValue(ruleViolation('Дата — не позже сегодняшней.'));
    renderPanel(api, true);
    const form = await openForm();

    await userEvent.type(within(form).getByLabelText('Комментарий'), 'Пресс-релиз');
    await userEvent.click(within(form).getByRole('button', { name: 'Записать' }));

    expect(await within(form).findByRole('alert')).toHaveTextContent('Дата — не позже сегодняшней.');
    expect(within(form).getByLabelText('Комментарий')).toHaveValue('Пресс-релиз');
    expect(screen.getByRole('dialog', { name: 'Новая запись о рейтинге' })).toBeInTheDocument();
  });

  it('closes the panel without a record', async () => {
    const api = renderPanel(fakeApi(), true);
    await openForm();

    await userEvent.click(screen.getByRole('button', { name: 'Закрыть' }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.addRating).not.toHaveBeenCalled();
  });

  it('cancels a mistaken record with a reason', async () => {
    const api = renderPanel(fakeApi(), true);
    const cancel = await screen.findByRole('button', { name: 'Отменить запись от 11.11.2026: АКРА AA-(RU)' });
    // Отменённую запись второй раз не отменяют.
    expect(screen.getAllByRole('button', { name: /^Отменить запись от/ })).toHaveLength(2);

    await userEvent.click(cancel);
    const form = screen.getByRole('form', { name: 'Отмена: запись от 11.11.2026: АКРА AA-(RU)' });
    const submit = within(form).getByRole('button', { name: 'Отменить запись' });
    expect(submit).toBeDisabled();
    await userEvent.click(within(form).getByRole('button', { name: 'Не отменять' }));
    expect(screen.queryByRole('form', { name: /^Отмена:/ })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Отменить запись от 11.11.2026: АКРА AA-(RU)' }));
    const again = screen.getByRole('form', { name: /^Отмена:/ });
    await userEvent.type(within(again).getByLabelText('Почему запись ошибочна'), 'Не тот выпуск');
    await userEvent.click(within(again).getByRole('button', { name: 'Отменить запись' }));

    expect(api.cancelRating).toHaveBeenCalledWith('r-3', 'Не тот выпуск');
    expect(api.ratings).toHaveBeenCalledTimes(2);
  });

  it('says why a cancellation failed', async () => {
    const api = fakeApi();
    api.cancelRating.mockRejectedValue(ruleViolation('Запись уже отменена.'));
    renderPanel(api, true);

    await userEvent.click(await screen.findByRole('button', { name: /^Отменить запись от 01\.06/ }));
    const form = screen.getByRole('form', { name: /^Отмена:/ });
    await userEvent.type(within(form).getByLabelText('Почему запись ошибочна'), 'Дубль');
    await userEvent.click(within(form).getByRole('button', { name: 'Отменить запись' }));

    expect(await within(form).findByRole('alert')).toHaveTextContent('Запись уже отменена.');
  });

  it('describes a withdrawal and "no rating" in the history', async () => {
    const api = fakeApi();
    api.ratings.mockResolvedValue({
      now: { status: 'unrated', ratings: [] },
      records: [
        { ...RATINGS.records[0]!, id: 'w', action: 'withdrawn', rating: null, grade: null },
        { ...RATINGS.records[0]!, id: 'n', action: 'unrated', agency: null, agency_name: null, rating: null },
      ],
    });
    renderPanel(api);

    expect(await screen.findByText('рейтинга нет ни у одного агентства')).toBeInTheDocument();
    expect(screen.getByText('АКРА: рейтинг отозван')).toBeInTheDocument();
  });
});
