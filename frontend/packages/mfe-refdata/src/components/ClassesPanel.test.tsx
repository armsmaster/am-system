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

describe('ClassesPanel', () => {
  it('shows each classifier of bonds: the value now or that it is not set, and the history', async () => {
    const api = renderPanel();

    expect(await screen.findByRole('table', { name: 'Значения сейчас' })).toBeInTheDocument();
    expect(api.classifications).toHaveBeenCalledWith(BOND);
    // «Тип эмитента» — классификатор эмитентов, у бумаги его нет.
    expect(rows('Значения сейчас')).toEqual([
      ['Очерёдность', 'старший долг', 'с начала обращения'],
      ['Транш', 'не задано', '—'],
    ]);
    expect(rows('История значений')).toEqual([
      [
        'с 11.11.2026',
        'Очерёдность',
        'субординированнаяОтменена: Ошиблись выпуском (risk, 11.11.2026, 13:00)',
        'risk11.11.2026, 12:00',
        'По проспекту',
      ],
      ['с начала обращения', 'Очерёдность', 'старший долг', 'risk02.11.2026, 12:00', '—'],
    ]);
    expect(screen.queryByRole('form')).not.toBeInTheDocument();
  });

  it('says when there are no classifiers for this kind of subject', async () => {
    const api = fakeApi();
    api.classifiers.mockResolvedValue([ISSUER_KIND]);
    api.classifications.mockResolvedValue({ now: [], records: [] });
    renderPanel(api, true);

    expect(await screen.findByText(/Классификаторов для бумаг пока нет/)).toBeInTheDocument();
    expect(screen.queryByRole('form')).not.toBeInTheDocument();
  });

  it('hides an empty history', async () => {
    const api = fakeApi();
    api.classifications.mockResolvedValue({ now: [], records: [] });
    renderPanel(api);

    expect(await screen.findByRole('table', { name: 'Значения сейчас' })).toBeInTheDocument();
    expect(screen.queryByRole('table', { name: 'История значений' })).not.toBeInTheDocument();
  });

  it('sets a value from a date or from the start', async () => {
    const api = renderPanel(fakeApi(), true);
    const form = await screen.findByRole('form', { name: 'Новое значение классификатора' });

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

    await userEvent.type(within(form).getByLabelText('Действует с'), '2026-11-11');
    await userEvent.type(within(form).getByLabelText('Комментарий'), 'По проспекту');
    await userEvent.click(within(form).getByRole('button', { name: 'Записать' }));
    expect(api.classify).toHaveBeenLastCalledWith(BOND, {
      classifier_id: 'c-2',
      value_id: 'v-4',
      valid_from: '2026-11-11',
      comment: 'По проспекту',
    });
    expect(within(form).getByLabelText('Комментарий')).toHaveValue('');
    expect(api.classifications).toHaveBeenCalledTimes(3);
  });

  it('shows why the value was not set', async () => {
    const api = fakeApi();
    api.classify.mockRejectedValue(ruleViolation('«Очерёдность» на эту дату уже «старший долг».'));
    renderPanel(api, true);
    const form = await screen.findByRole('form', { name: 'Новое значение классификатора' });

    await userEvent.type(within(form).getByLabelText('Комментарий'), 'Повтор');
    await userEvent.click(within(form).getByRole('button', { name: 'Записать' }));

    expect(await within(form).findByRole('alert')).toHaveTextContent('уже «старший долг»');
    expect(within(form).getByLabelText('Комментарий')).toHaveValue('Повтор');
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
