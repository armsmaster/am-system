import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { asApi, fakeApi, ruleViolation, type FakeApi } from '../test/fixtures';
import { ClassifiersPage } from './ClassifiersPage';

function renderPage(api: FakeApi = fakeApi(), canEdit = false) {
  render(<ClassifiersPage api={asApi(api)} canEdit={canEdit} />);
  return api;
}

describe('ClassifiersPage', () => {
  it('lists classifiers with what they describe and their values', async () => {
    renderPage();

    const seniority = await screen.findByRole('listitem', { name: 'Очерёдность' });
    expect(seniority).toHaveTextContent('Очерёдность · для бумаг');
    expect(seniority).toHaveTextContent('п. 2 политики');
    expect(seniority).toHaveTextContent('Значения: старший долг, субординированная');
    expect(screen.getByRole('listitem', { name: 'Тип эмитента' })).toHaveTextContent('· для эмитентов');
    expect(screen.getByText('Классификаторы ведёт риск-менеджмент.')).toBeInTheDocument();
    expect(screen.queryByRole('form')).not.toBeInTheDocument();
  });

  it('says when there are no classifiers yet', async () => {
    const api = fakeApi();
    api.classifiers.mockResolvedValue([]);
    renderPage(api);

    expect(await screen.findByText('Классификаторов пока нет.')).toBeInTheDocument();
  });

  it('creates a classifier with a value per line', async () => {
    const api = renderPage(fakeApi(), true);
    const form = await screen.findByRole('form', { name: 'Новый классификатор' });

    await userEvent.type(within(form).getByLabelText('Название'), ' Секьюритизация ');
    await userEvent.selectOptions(within(form).getByLabelText('К чему относится'), 'issuer');
    await userEvent.selectOptions(within(form).getByLabelText('К чему относится'), 'instrument');
    await userEvent.type(
      within(form).getByLabelText('Значения — по одному в строке'),
      'нет{Enter}{Enter} ИЦБ и прочие секьюритизации ',
    );
    await userEvent.type(within(form).getByLabelText('Описание'), 'п. 2 политики');
    await userEvent.click(within(form).getByRole('button', { name: 'Завести' }));

    expect(api.createClassifier).toHaveBeenCalledWith({
      name: 'Секьюритизация',
      target: 'instrument',
      description: 'п. 2 политики',
      values: ['нет', 'ИЦБ и прочие секьюритизации'],
    });
    expect(within(form).getByLabelText('Название')).toHaveValue('');
    expect(api.classifiers).toHaveBeenCalledTimes(2);
  });

  it('keeps the form when the name is taken', async () => {
    const api = fakeApi();
    api.createClassifier.mockRejectedValue(ruleViolation('Классификатор «Очерёдность» уже есть.'));
    renderPage(api, true);
    const form = await screen.findByRole('form', { name: 'Новый классификатор' });

    await userEvent.type(within(form).getByLabelText('Название'), 'Очерёдность');
    await userEvent.type(within(form).getByLabelText('Значения — по одному в строке'), 'старший долг');
    await userEvent.click(within(form).getByRole('button', { name: 'Завести' }));

    expect(await within(form).findByRole('alert')).toHaveTextContent('уже есть');
    expect(within(form).getByLabelText('Название')).toHaveValue('Очерёдность');
    expect(api.createClassifier).toHaveBeenCalledWith(expect.objectContaining({ description: null }));
  });

  it('renames a classifier and clears its description', async () => {
    const api = renderPage(fakeApi(), true);
    const form = await screen.findByRole('form', { name: 'Название: Очерёдность' });

    await userEvent.clear(within(form).getByLabelText('Название'));
    await userEvent.type(within(form).getByLabelText('Название'), 'Очерёдность выплат');
    await userEvent.clear(within(form).getByLabelText('Описание'));
    await userEvent.click(within(form).getByRole('button', { name: 'Сохранить' }));

    expect(api.updateClassifier).toHaveBeenCalledWith('c-1', { name: 'Очерёдность выплат', description: null });
    // У классификатора без описания поле пустое.
    expect(within(screen.getByRole('form', { name: 'Название: Транш' })).getByLabelText('Описание')).toHaveValue('');
  });

  it('adds a value and renames one', async () => {
    const api = renderPage(fakeApi(), true);
    const form = await screen.findByRole('form', { name: 'Значения: Транш' });

    await userEvent.type(within(form).getByLabelText('Значение'), 'мезонинный');
    await userEvent.click(within(form).getByRole('button', { name: 'Добавить' }));
    expect(api.addValue).toHaveBeenCalledWith('c-2', 'мезонинный');
    expect(within(form).getByLabelText('Значение')).toHaveValue('');

    await userEvent.selectOptions(within(form).getByLabelText('Что сделать'), 'v-4');
    await userEvent.type(within(form).getByLabelText('Значение'), 'младший');
    await userEvent.click(within(form).getByRole('button', { name: 'Переименовать' }));
    expect(api.renameValue).toHaveBeenCalledWith('c-2', 'v-4', 'младший');
  });

  it('shows why a value was refused', async () => {
    const api = fakeApi();
    api.addValue.mockRejectedValue(ruleViolation('У классификатора «Транш» уже есть значение «иной».'));
    renderPage(api, true);
    const form = await screen.findByRole('form', { name: 'Значения: Транш' });

    await userEvent.type(within(form).getByLabelText('Значение'), 'Иной');
    await userEvent.click(within(form).getByRole('button', { name: 'Добавить' }));

    expect(await within(form).findByRole('alert')).toHaveTextContent('уже есть значение');
    expect(within(form).getByLabelText('Значение')).toHaveValue('Иной');
  });

  it('shows why a rename was refused', async () => {
    const api = fakeApi();
    api.updateClassifier.mockRejectedValue(ruleViolation('Изменений нет.'));
    renderPage(api, true);
    const form = await screen.findByRole('form', { name: 'Название: Очерёдность' });

    await userEvent.click(within(form).getByRole('button', { name: 'Сохранить' }));

    expect(await within(form).findByRole('alert')).toHaveTextContent('Изменений нет.');
    expect(api.updateClassifier).toHaveBeenCalledWith('c-1', { name: 'Очерёдность', description: 'п. 2 политики' });
  });
});
