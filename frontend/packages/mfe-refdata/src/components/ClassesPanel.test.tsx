import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { asApi, fakeApi, ISSUER_KIND, ruleViolation, type FakeApi } from '../test/fixtures';
import { ClassesPanel } from './ClassesPanel';

const BOND = { instrument_id: 'i-1' } as const;

function renderPanel(api: FakeApi = fakeApi(), canEdit = false) {
  render(<ClassesPanel api={asApi(api)} subject={BOND} target="instrument" canEdit={canEdit} today="2026-11-17" />);
  return api;
}

function rows(name: string) {
  return within(screen.getByRole('table', { name }))
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell').map((c) => c.textContent));
}

/** Значения сейчас: классификатор → значение с датой, с которой оно действует. */
async function tiles() {
  const list = within(await screen.findByLabelText('Значения сейчас'));
  const terms = list.getAllByRole('term').map((dt) => dt.textContent);
  const values = list.getAllByRole('definition').map((dd) => dd.textContent);
  return Object.fromEntries(terms.map((term, i) => [term, values[i]]));
}

/** Открывает выдвижную панель с формой нового значения и отдаёт форму. */
async function openForm() {
  await userEvent.click(await screen.findByRole('button', { name: 'Задать значение' }));
  return within(screen.getByRole('dialog', { name: 'Новое значение классификатора' })).getByRole('form', {
    name: 'Новое значение классификатора',
  });
}

describe('ClassesPanel', () => {
  it('shows each classifier of bonds: the value now or that it is not set, and the history', async () => {
    const api = renderPanel();

    // «Тип эмитента» — классификатор эмитентов, у бумаги его нет.
    expect(await tiles()).toEqual({ Очерёдность: 'старший долгс начала обращения', Транш: 'не задано' });
    expect(api.classifications).toHaveBeenCalledWith(BOND);
    expect(rows('История значений')).toEqual([
      [
        'с 11.11.2026',
        'Очерёдность: субординированная · с 11.11.2026risk, 11.11.2026, 12:00 · По проспекту' +
          'Отменена: Ошиблись выпуском (risk, 11.11.2026, 13:00)',
      ],
      ['с начала обращения', 'Очерёдность: старший долг · с начала обращенияrisk, 02.11.2026, 12:00'],
    ]);
    expect(screen.getByText('Очерёдность: субординированная').closest('tr')).toHaveClass('cancelled');
    expect(screen.queryByRole('button', { name: 'Задать значение' })).not.toBeInTheDocument();
  });

  it('says when there are no classifiers for this kind of subject', async () => {
    const api = fakeApi();
    api.classifiers.mockResolvedValue([ISSUER_KIND]);
    api.classifications.mockResolvedValue({ now: [], records: [] });
    renderPanel(api, true);

    expect(await screen.findByText(/Классификаторов для бумаг пока нет/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Задать значение' })).not.toBeInTheDocument();
  });

  it('hides an empty history', async () => {
    const api = fakeApi();
    api.classifications.mockResolvedValue({ now: [], records: [] });
    renderPanel(api);

    expect(await tiles()).toEqual({ Очерёдность: 'не задано', Транш: 'не задано' });
    expect(screen.queryByRole('table', { name: 'История значений' })).not.toBeInTheDocument();
    expect(screen.queryByText('История записей')).not.toBeInTheDocument();
  });

  it('sets a value from a date or from the start', async () => {
    const api = renderPanel(fakeApi(), true);
    let form = await openForm();

    await userEvent.selectOptions(within(form).getByLabelText('Классификатор'), 'c-2');
    // Другой классификатор — его значения, первое выбрано.
    expect(within(form).getByLabelText('Значение')).toHaveValue('v-3');
    await userEvent.selectOptions(within(form).getByLabelText('Значение'), 'v-4');
    await userEvent.click(within(form).getByRole('button', { name: 'Записать' }));
    expect(api.classify).toHaveBeenLastCalledWith(BOND, {
      classifier_id: 'c-2',
      value_id: 'v-4',
      valid_from: null,
      comment: null,
    });
    // Записали — панель закрылась.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    form = await openForm();
    await userEvent.selectOptions(within(form).getByLabelText('Классификатор'), 'c-2');
    await userEvent.selectOptions(within(form).getByLabelText('Значение'), 'v-4');
    await userEvent.type(within(form).getByLabelText('Действует с'), '2026-11-11');
    await userEvent.type(within(form).getByLabelText('Комментарий'), 'По проспекту');
    await userEvent.click(within(form).getByRole('button', { name: 'Записать' }));
    expect(api.classify).toHaveBeenLastCalledWith(BOND, {
      classifier_id: 'c-2',
      value_id: 'v-4',
      valid_from: '2026-11-11',
      comment: 'По проспекту',
    });
    expect(api.classifications).toHaveBeenCalledTimes(3);
  });

  it('keeps the panel and shows why the value was not set', async () => {
    const api = fakeApi();
    api.classify.mockRejectedValue(ruleViolation('«Очерёдность» на эту дату уже «старший долг».'));
    renderPanel(api, true);
    const form = await openForm();

    await userEvent.type(within(form).getByLabelText('Комментарий'), 'Повтор');
    await userEvent.click(within(form).getByRole('button', { name: 'Записать' }));

    expect(await within(form).findByRole('alert')).toHaveTextContent('уже «старший долг»');
    expect(within(form).getByLabelText('Комментарий')).toHaveValue('Повтор');
    expect(screen.getByRole('dialog', { name: 'Новое значение классификатора' })).toBeInTheDocument();
  });

  it('closes the panel without a value', async () => {
    const api = renderPanel(fakeApi(), true);
    await openForm();

    await userEvent.click(screen.getByRole('button', { name: 'Закрыть' }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.classify).not.toHaveBeenCalled();
  });

  it('cancels a mistaken value', async () => {
    const api = renderPanel(fakeApi(), true);

    await userEvent.click(
      await screen.findByRole('button', { name: 'Отменить «Очерёдность: старший долг» с начала обращения' }),
    );
    const form = screen.getByRole('form', { name: /^Отмена:/ });
    await userEvent.type(within(form).getByLabelText('Почему запись ошибочна'), 'Не тот выпуск');
    await userEvent.click(within(form).getByRole('button', { name: 'Отменить запись' }));

    expect(api.cancelClassification).toHaveBeenCalledWith('k-1', 'Не тот выпуск');
  });
});
