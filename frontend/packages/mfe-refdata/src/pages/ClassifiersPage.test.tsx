import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { asApi, fakeApi, ruleViolation, SENIORITY, type FakeApi } from '../test/fixtures';
import { ClassifiersPage } from './ClassifiersPage';

function renderPage(api: FakeApi = fakeApi(), canEdit = false) {
  render(<ClassifiersPage api={asApi(api)} canEdit={canEdit} />);
  return api;
}

/** Названия карточек в списке — в том порядке, в каком они показаны. */
function cards() {
  return within(screen.getByRole('list', { name: 'Список классификаторов' }))
    .getAllByRole('heading', { level: 3 })
    .map((h) => h.textContent);
}

function chips(name: string) {
  return within(screen.getByRole('list', { name }))
    .getAllByRole('listitem')
    .map((li) => li.textContent);
}

async function openCreate() {
  await userEvent.click(await screen.findByRole('button', { name: 'Новый классификатор' }));
  return within(screen.getByRole('dialog', { name: 'Новый классификатор' })).getByRole('form', {
    name: 'Новый классификатор',
  });
}

async function openEdit(name: string) {
  await userEvent.click(await screen.findByRole('button', { name: `Изменить «${name}»` }));
  return screen.getByRole('dialog', { name });
}

describe('ClassifiersPage', () => {
  it('shows classifiers as cards: what they describe, a description and values as chips', async () => {
    renderPage();

    const seniority = await screen.findByRole('listitem', { name: 'Очерёдность' });
    expect(seniority).toHaveTextContent('для бумаг');
    expect(seniority).toHaveTextContent('п. 2 политики');
    expect(chips('Значения: Очерёдность')).toEqual(['старший долг', 'субординированная']);
    expect(screen.getByRole('listitem', { name: 'Тип эмитента' })).toHaveTextContent('для эмитентов');
    expect(cards()).toEqual(['Очерёдность', 'Транш', 'Тип эмитента']);
    // Смотрят все, ведёт риск-менеджмент.
    expect(screen.getByText(/Классификаторы ведёт риск-менеджмент\./)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Новый классификатор' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Изменить/ })).not.toBeInTheDocument();
  });

  it('filters by what the classifier describes', async () => {
    renderPage();

    await userEvent.click(await screen.findByRole('button', { name: 'Эмитенты 1' }));
    expect(cards()).toEqual(['Тип эмитента']);
    await userEvent.click(screen.getByRole('button', { name: 'Бумаги 2' }));
    expect(cards()).toEqual(['Очерёдность', 'Транш']);
    await userEvent.click(screen.getByRole('button', { name: 'Все 3' }));
    expect(cards()).toHaveLength(3);
  });

  it('says when the filter leaves nothing', async () => {
    const api = fakeApi();
    api.classifiers.mockResolvedValue([SENIORITY]);
    renderPage(api);

    await userEvent.click(await screen.findByRole('button', { name: 'Эмитенты 0' }));

    expect(screen.getByText('Таких классификаторов нет.')).toBeInTheDocument();
  });

  it('invites risk management to create the first classifier', async () => {
    const api = fakeApi();
    api.classifiers.mockResolvedValue([]);
    const { unmount } = render(<ClassifiersPage api={asApi(api)} canEdit={false} />);

    expect(await screen.findByText('Классификаторов пока нет.')).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'К чему относится' })).not.toBeInTheDocument();
    unmount();

    renderPage(api, true);
    expect(await screen.findByText(/Заведите первый/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Новый классификатор' })).toBeInTheDocument();
  });

  it('creates a classifier from values gathered as chips', async () => {
    const api = renderPage(fakeApi(), true);
    const form = await openCreate();
    const draft = within(form).getByLabelText('Новое значение');

    await userEvent.type(within(form).getByLabelText('Название'), ' Секьюритизация ');
    await userEvent.click(within(form).getByRole('radio', { name: 'Эмитент' }));
    expect(within(form).getByRole('radio', { name: 'Эмитент' })).toBeChecked();
    await userEvent.click(within(form).getByRole('radio', { name: 'Бумага' }));
    expect(within(form).getByText('Пока ни одного — нужно хотя бы одно.')).toBeInTheDocument();
    // Enter переносит значение в чип; пустое поле ничего не добавляет.
    await userEvent.type(draft, ' нет {Enter}{Enter}');
    await userEvent.type(draft, 'ИЦБ');
    await userEvent.click(within(form).getByRole('button', { name: 'Добавить значение' }));
    expect(chips('Значения нового классификатора')).toEqual(['нет', 'ИЦБ']);
    // Повтор без учёта регистра не добавляется, введённое остаётся в поле.
    await userEvent.type(draft, 'Нет{Enter}');
    expect(within(form).getByText('«Нет» уже есть в списке.')).toBeInTheDocument();
    expect(draft).toHaveValue('Нет');
    await userEvent.clear(draft);
    expect(within(form).queryByText('«Нет» уже есть в списке.')).not.toBeInTheDocument();
    // Лишнее убирают крестиком.
    await userEvent.click(within(form).getByRole('button', { name: 'Убрать «ИЦБ»' }));
    expect(chips('Значения нового классификатора')).toEqual(['нет']);
    // Вставленный список раскладывается по чипам, одно значение — просто в поле.
    await userEvent.click(draft);
    await userEvent.paste('ИЦБ\nипотечные; ');
    expect(chips('Значения нового классификатора')).toEqual(['нет', 'ИЦБ', 'ипотечные']);
    await userEvent.paste('прочие');
    expect(draft).toHaveValue('прочие');
    await userEvent.type(within(form).getByLabelText(/Описание/), 'п. 2 политики');
    // Недописанное в поле значение тоже уходит.
    await userEvent.click(within(form).getByRole('button', { name: 'Завести' }));

    expect(api.createClassifier).toHaveBeenCalledWith({
      name: 'Секьюритизация',
      target: 'instrument',
      description: 'п. 2 политики',
      values: ['нет', 'ИЦБ', 'ипотечные', 'прочие'],
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.classifiers).toHaveBeenCalledTimes(2);
  });

  it('says what is missing before creating', async () => {
    const api = renderPage(fakeApi(), true);
    const form = await openCreate();

    await userEvent.click(within(form).getByRole('button', { name: 'Завести' }));
    expect(within(form).getByText('Введите название.')).toBeInTheDocument();
    expect(within(form).getByText('Добавьте хотя бы одно значение.')).toBeInTheDocument();

    // Есть значение, но нет названия.
    await userEvent.type(within(form).getByLabelText('Новое значение'), 'старший долг{Enter}');
    await userEvent.click(within(form).getByRole('button', { name: 'Завести' }));
    expect(within(form).getByText('Введите название.')).toBeInTheDocument();
    await userEvent.type(within(form).getByLabelText('Название'), 'О');
    expect(within(form).queryByText('Введите название.')).not.toBeInTheDocument();

    // Повтор в недописанном значении — тоже не отправляем.
    await userEvent.type(within(form).getByLabelText('Новое значение'), 'Старший долг');
    await userEvent.click(within(form).getByRole('button', { name: 'Завести' }));
    expect(within(form).getByText('«Старший долг» уже есть в списке.')).toBeInTheDocument();
    expect(api.createClassifier).not.toHaveBeenCalled();
  });

  it('keeps the panel and the values when the service refuses', async () => {
    const api = fakeApi();
    api.createClassifier.mockRejectedValue(ruleViolation('Классификатор «Очерёдность» уже есть.'));
    renderPage(api, true);
    const form = await openCreate();

    await userEvent.type(within(form).getByLabelText('Название'), 'Очерёдность');
    await userEvent.type(within(form).getByLabelText('Новое значение'), 'старший долг{Enter}');
    await userEvent.click(within(form).getByRole('button', { name: 'Завести' }));

    expect(await within(form).findByRole('alert')).toHaveTextContent('уже есть');
    expect(chips('Значения нового классификатора')).toEqual(['старший долг']);
    expect(api.createClassifier).toHaveBeenCalledWith(expect.objectContaining({ description: null }));

    await userEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('renames a classifier and clears its description in the panel', async () => {
    const api = renderPage(fakeApi(), true);
    const panel = await openEdit('Очерёдность');
    const form = within(panel).getByRole('form', { name: 'Название и описание' });

    expect(panel).toHaveTextContent('для бумаг завёл risk, 02.11.2026, 12:00');
    await userEvent.clear(within(form).getByLabelText('Название'));
    await userEvent.type(within(form).getByLabelText('Название'), 'Очерёдность выплат');
    await userEvent.clear(within(form).getByLabelText('Описание'));
    await userEvent.click(within(form).getByRole('button', { name: 'Сохранить' }));

    expect(api.updateClassifier).toHaveBeenCalledWith('c-1', { name: 'Очерёдность выплат', description: null });
    // Панель остаётся открытой: дальше можно править значения.
    expect(screen.getByRole('dialog', { name: 'Очерёдность' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    const tranche = await openEdit('Транш');
    // Без описания поле пустое.
    expect(within(tranche).getByLabelText('Описание')).toHaveValue('');
  });

  it('shows why the classifier was not saved', async () => {
    const api = fakeApi();
    api.updateClassifier.mockRejectedValue(ruleViolation('Изменений нет.'));
    renderPage(api, true);
    const panel = await openEdit('Очерёдность');

    await userEvent.click(within(panel).getByRole('button', { name: 'Сохранить' }));

    expect(await within(panel).findByRole('alert')).toHaveTextContent('Изменений нет.');
    expect(api.updateClassifier).toHaveBeenCalledWith('c-1', { name: 'Очерёдность', description: 'п. 2 политики' });
  });

  it('renames a value in place: Enter saves, Escape and the cross cancel', async () => {
    const api = renderPage(fakeApi(), true);
    const panel = await openEdit('Транш');

    // Escape отменяет переименование, а не закрывает панель.
    await userEvent.click(within(panel).getByRole('button', { name: 'Переименовать «иной»' }));
    await userEvent.type(within(panel).getByLabelText('Новое название значения'), 'ой{Escape}');
    expect(screen.getByRole('dialog', { name: 'Транш' })).toBeInTheDocument();
    expect(chips('Значения')).toEqual(['старший', 'иной']);

    await userEvent.click(within(panel).getByRole('button', { name: 'Переименовать «иной»' }));
    await userEvent.click(within(panel).getByRole('button', { name: 'Не переименовывать' }));
    expect(within(panel).queryByLabelText('Новое название значения')).not.toBeInTheDocument();

    await userEvent.click(within(panel).getByRole('button', { name: 'Переименовать «иной»' }));
    const input = within(panel).getByLabelText('Новое название значения');
    expect(input).toHaveValue('иной');
    await userEvent.clear(input);
    await userEvent.type(input, ' младший {Enter}');

    expect(api.renameValue).toHaveBeenCalledWith('c-2', 'v-4', 'младший');
    expect(within(panel).queryByLabelText('Новое название значения')).not.toBeInTheDocument();
    expect(api.classifiers).toHaveBeenCalledTimes(2);
  });

  it('keeps the value being renamed when the service refuses', async () => {
    const api = fakeApi();
    api.renameValue.mockRejectedValue(ruleViolation('У классификатора «Транш» уже есть значение «старший».'));
    renderPage(api, true);
    const panel = await openEdit('Транш');

    await userEvent.click(within(panel).getByRole('button', { name: 'Переименовать «иной»' }));
    await userEvent.clear(within(panel).getByLabelText('Новое название значения'));
    await userEvent.type(within(panel).getByLabelText('Новое название значения'), 'Старший');
    await userEvent.click(within(panel).getByRole('button', { name: 'Сохранить название' }));

    expect(await within(panel).findByRole('alert')).toHaveTextContent('уже есть значение');
    expect(within(panel).getByLabelText('Новое название значения')).toHaveValue('Старший');
  });

  it('adds a value at the end of the list', async () => {
    const api = renderPage(fakeApi(), true);
    const panel = await openEdit('Транш');

    await userEvent.type(within(panel).getByLabelText('Добавить значение'), ' мезонин {Enter}');

    expect(api.addValue).toHaveBeenCalledWith('c-2', 'мезонин');
    expect(within(panel).getByLabelText('Добавить значение')).toHaveValue('');
  });

  it('shows why a value was not added', async () => {
    const api = fakeApi();
    api.addValue.mockRejectedValue(ruleViolation('У классификатора «Транш» уже есть значение «иной».'));
    renderPage(api, true);
    const panel = await openEdit('Транш');

    await userEvent.type(within(panel).getByLabelText('Добавить значение'), 'Иной');
    await userEvent.click(within(panel).getByRole('button', { name: 'Добавить' }));

    expect(await within(panel).findByRole('alert')).toHaveTextContent('уже есть значение');
    expect(within(panel).getByLabelText('Добавить значение')).toHaveValue('Иной');
  });
});
