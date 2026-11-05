import { ApiError } from '@am/platform';
import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { breach, fakeApi, renderLimits } from '../test/fixtures';

const BREACHES = '/limits/breaches';

function rows() {
  return within(screen.getByRole('table', { name: 'Журнал нарушений' }))
    .getAllByRole('row')
    .slice(1)
    .filter((row) => !row.querySelector('[role="group"]'))
    .map((row) => within(row).getAllByRole('cell').map((c) => c.textContent));
}

describe('BreachesPage', () => {
  it('shows open breaches of the fund and switches the filter', async () => {
    const api = fakeApi();
    api.breaches.mockResolvedValue([
      breach({
        id: 'b-a',
        limit_code: 'Л3',
        name: 'Эмитент А',
        first_day: '2026-11-13',
        detected_on: '2026-11-13',
        value: '10.1786',
        maximum: '10',
        kind: 'active',
        suggested: 'active',
        deadline: '2026-11-16',
        state: 'overdue',
      }),
      breach({ extensions: [{ deadline: '2027-01-29', decision: '№ 5', comment: 'Рынок', by: 'risk', at: 'x' }] }),
    ]);
    renderLimits(api, BREACHES);

    expect(await screen.findByRole('table', { name: 'Журнал нарушений' })).toBeInTheDocument();
    expect(api.breaches).toHaveBeenCalledWith('f-dohod', 'open');
    expect(rows()).toEqual([
      [
        'Л3, на эмитента',
        'Эмитент А',
        '10,18% при лимите 10%',
        'с 13.11.2026выявлено 13.11.2026',
        'активноепредложение системы',
        '16.11.2026',
        'просроченоПодробнее',
      ],
      [
        'Л4, на эмитента',
        'Эмитент Г',
        '2,68% при лимите 2,5%',
        'с 12.11.2026выявлено 12.11.2026',
        'пассивноепредложение системы',
        '10.12.2026продлён',
        'открытоПодробнее',
      ],
    ]);
    expect(screen.getAllByRole('row')[1]).toHaveClass('status-breach');

    await userEvent.selectOptions(screen.getByLabelText('Нарушения'), 'all');
    expect(api.breaches).toHaveBeenLastCalledWith('f-dohod', undefined);
    await userEvent.selectOptions(screen.getByLabelText('Фонд'), 'f-opif');
    expect(api.breaches).toHaveBeenLastCalledWith('f-opif', undefined);
  });

  it('says when there are no such breaches', async () => {
    const api = fakeApi();
    api.breaches.mockResolvedValue([]);
    renderLimits(api, BREACHES);

    expect(await screen.findByText(/Таких нарушений нет/)).toBeInTheDocument();
  });

  it('shows the history to the trader without the forms', async () => {
    const api = fakeApi();
    api.breaches.mockResolvedValue([
      breach({
        state: 'eliminated',
        eliminated_on: '2026-11-17',
        value: '2.4643',
        peak: '2.7857',
        kind: 'active',
        kind_by: 'risk',
        notes: [
          { by: 'system', at: '2026-11-16T07:00:00Z', text: 'нарушение увеличено сделкой 13.11.2026' },
          { by: 'risk', at: '2026-11-16T09:00:00Z', text: 'Докупили', kind: 'active' },
        ],
        extensions: [
          { deadline: '2027-01-29', decision: '№ 5 от 20.11.2026', comment: 'Рынок', by: 'risk', at: '2026-11-20T09:00:00Z' },
        ],
      }),
    ]);
    renderLimits(api, BREACHES);

    await userEvent.click(await screen.findByRole('button', { name: 'Подробнее' }));
    const details = screen.getByRole('group', { name: 'Нарушение: Л4, на эмитента, Эмитент Г' });
    expect(details).toHaveTextContent('Наибольшее значение — 2,79%.');
    expect(details).toHaveTextContent('Система предлагала: пассивное.');
    expect(within(details).getByRole('list', { name: 'Комментарии' })).toHaveTextContent(
      /system: нарушение увеличено сделкой 13\.11\.2026.*risk: Докупили — вид: активное/,
    );
    expect(within(details).getByRole('list', { name: 'Продления' })).toHaveTextContent(
      /до 29\.01\.2027, решение № 5 от 20\.11\.2026: Рынок \(risk, /,
    );
    expect(details).toHaveTextContent('Вид нарушения подтверждает и срок продлевает риск-менеджмент.');
    expect(screen.getAllByRole('row')[1]).toHaveTextContent('устранено 17.11.2026');
    expect(screen.queryByRole('form')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Скрыть' }));
    expect(screen.queryByRole('group')).not.toBeInTheDocument();
  });

  it('lets risk management confirm the kind and extend the deadline', async () => {
    const api = renderLimits(fakeApi(), BREACHES, true);

    await userEvent.click(await screen.findByRole('button', { name: 'Подробнее' }));
    const review = screen.getByRole('form', { name: 'Вид нарушения' });
    await userEvent.selectOptions(within(review).getByLabelText('Вид'), 'active');
    await userEvent.type(within(review).getByLabelText('Комментарий'), ' Покупка 11.11 ');
    await userEvent.click(within(review).getByRole('button', { name: 'Записать' }));
    expect(api.review).toHaveBeenCalledWith('b-g', { kind: 'active', comment: 'Покупка 11.11' });
    // Записали — журнал заново, комментарий уже в истории.
    expect(api.breaches).toHaveBeenCalledTimes(2);
    expect(within(review).getByLabelText('Комментарий')).toHaveValue('');

    const extension = screen.getByRole('form', { name: 'Продление срока' });
    expect(within(extension).getByLabelText('Новый срок')).toHaveValue('2026-12-10');
    fireEvent.change(within(extension).getByLabelText('Новый срок'), { target: { value: '2027-01-29' } });
    await userEvent.type(within(extension).getByLabelText('Решение — номер и дата'), '№ 5 от 20.11.2026');
    await userEvent.type(within(extension).getByLabelText('Комментарий'), 'Рынок');
    await userEvent.click(within(extension).getByRole('button', { name: 'Продлить' }));
    expect(api.extend).toHaveBeenCalledWith('b-g', {
      deadline: '2027-01-29',
      decision: '№ 5 от 20.11.2026',
      comment: 'Рынок',
    });
    expect(within(extension).getByLabelText('Решение — номер и дата')).toHaveValue('');
  });

  it('shows why the service refused; active breaches are not extended', async () => {
    const api = fakeApi();
    api.breaches.mockResolvedValue([
      breach({ kind: 'active', check_type: 'metric', limit_code: 'Л1', label: '', value: '4.1', peak: '4.3', maximum: '4' }),
    ]);
    api.review.mockRejectedValue(new ApiError(403, { detail: 'Недостаточно прав' }));
    renderLimits(api, BREACHES, true);

    await userEvent.click(await screen.findByRole('button', { name: 'Подробнее' }));
    expect(screen.queryByRole('form', { name: 'Продление срока' })).not.toBeInTheDocument();
    expect(screen.getByText('Наибольшее значение — 4,30.')).toBeInTheDocument();
    const review = screen.getByRole('form', { name: 'Вид нарушения' });
    await userEvent.type(within(review).getByLabelText('Комментарий'), 'Сделка была');
    await userEvent.click(within(review).getByRole('button', { name: 'Записать' }));
    expect(await within(review).findByRole('alert')).toBeInTheDocument();
    // Отказали — комментарий остаётся, чтобы поправить и отправить снова.
    expect(within(review).getByLabelText('Комментарий')).toHaveValue('Сделка была');
  });

  it('keeps the extension form when the service refused', async () => {
    const api = fakeApi();
    api.extend.mockRejectedValue(new ApiError(422, { detail: { code: 'bad_deadline', message: 'Срок больше предельного.' } }));
    renderLimits(api, BREACHES, true);

    await userEvent.click(await screen.findByRole('button', { name: 'Подробнее' }));
    const extension = screen.getByRole('form', { name: 'Продление срока' });
    await userEvent.type(within(extension).getByLabelText('Решение — номер и дата'), '№ 5');
    await userEvent.type(within(extension).getByLabelText('Комментарий'), 'Рынок');
    await userEvent.click(within(extension).getByRole('button', { name: 'Продлить' }));
    expect(await within(extension).findByRole('alert')).toHaveTextContent('Срок больше предельного.');
    expect(within(extension).getByLabelText('Решение — номер и дата')).toHaveValue('№ 5');
  });
});
