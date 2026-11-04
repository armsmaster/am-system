import { ApiError } from '@am/platform';
import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { BODY, fakeApi, limitSet, renderLimits } from '../test/fixtures';

const SETS = '/limits/sets';

describe('SetsPage', () => {
  it('shows the versions, newest first, and the chosen one in words', async () => {
    const api = fakeApi();
    api.sets.mockResolvedValue([limitSet(1), limitSet(2, { valid_from: '2026-11-12', comment: 'Порог 95%' })]);
    renderLimits(api, SETS);

    const table = await screen.findByRole('table');
    expect(
      within(table)
        .getAllByRole('row')
        .slice(1)
        .map((r) => within(r).getAllByRole('cell')[1]!.textContent),
    ).toEqual([
      '12.11.2026',
      '01.11.2026',
    ]);
    expect(screen.getByRole('row', { name: /^2 12\.11\.2026/ })).toHaveClass('selected');
    const words = screen.getByRole('article', { name: 'Набор лимитов словами' });
    expect(within(words).getByRole('heading', { level: 3 })).toHaveTextContent(
      'Инвестиционная политика «Доход 2», версия 1.0 от 23.10.2026',
    );
    expect(words).toHaveTextContent('на эмитента: Доля в активах на одного эмитента — не более 10%.');
    expect(words).toHaveTextContent('Л1 · Плечо — п. 5.1');

    await userEvent.click(screen.getByRole('button', { name: 'Показать версию 1' }));
    expect(screen.getByRole('row', { name: /^1 01\.11\.2026/ })).toHaveClass('selected');
    // Набор ведёт риск-менеджмент.
    expect(screen.queryByRole('form')).not.toBeInTheDocument();
    expect(screen.getByText(/Набор лимитов ведёт риск-менеджмент/)).toBeInTheDocument();
    expect(api.sets).toHaveBeenCalledWith('f-dohod');
  });

  it('says when the fund has no set yet and switches funds', async () => {
    const api = fakeApi();
    api.sets.mockResolvedValue([]);
    renderLimits(api, SETS, true);

    expect(await screen.findByText('Набора лимитов у фонда ещё нет.')).toBeInTheDocument();
    // Без прежней версии форма пустая и просит набор.
    const form = screen.getByRole('form', { name: 'Новая версия набора' });
    expect(within(form).getByText('Вставьте набор — документ JSON.')).toBeInTheDocument();
    expect(within(form).getByRole('button', { name: 'Сохранить версию' })).toBeDisabled();

    await userEvent.selectOptions(screen.getByLabelText('Фонд'), 'f-opif');
    expect(api.sets).toHaveBeenLastCalledWith('f-opif');
  });

  it('saves a new version from the latest one and shows it in words', async () => {
    const api = fakeApi();
    api.sets.mockResolvedValueOnce([limitSet(1)]).mockResolvedValue([limitSet(1), limitSet(2)]);
    renderLimits(api, SETS, true);

    const form = await screen.findByRole('form', { name: 'Новая версия набора' });
    const text = within(form).getByLabelText('Набор — документ JSON');
    expect(JSON.parse((text as HTMLTextAreaElement).value)).toEqual(BODY);
    const changed = { ...BODY, settings: { ...BODY.settings, warn_at: '95' } };
    fireEvent.change(text, { target: { value: JSON.stringify(changed) } });
    expect(within(form).getByText('Предупреждение — с 95% лимита.')).toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText('Действует с'), { target: { value: '2026-11-12' } });
    await userEvent.type(within(form).getByLabelText('Что и почему меняем'), ' Порог 95% ');
    await userEvent.click(within(form).getByRole('button', { name: 'Сохранить версию' }));

    expect(api.saveSet).toHaveBeenCalledWith('f-dohod', {
      valid_from: '2026-11-12',
      comment: 'Порог 95%',
      body: changed,
    });
    // Сохранили — список заново, выбрана новая версия.
    expect(await screen.findByRole('row', { name: /^2 01\.11\.2026/ })).toHaveClass('selected');
  });

  it('checks the document before saving and shows why the service refused', async () => {
    const api = fakeApi();
    api.saveSet.mockRejectedValue(
      new ApiError(422, { detail: { code: 'unknown_classifier', message: 'Классификатора c-x нет в справочнике.' } }),
    );
    renderLimits(api, SETS, true);
    const form = await screen.findByRole('form', { name: 'Новая версия набора' });
    const text = within(form).getByLabelText('Набор — документ JSON');

    fireEvent.change(text, { target: { value: '{"limits": [' } });
    expect(within(form).getByText(/^Это не JSON:/)).toBeInTheDocument();
    fireEvent.change(text, { target: { value: '{"limits": []}' } });
    expect(within(form).getByText('В наборе нужны document, settings и limits.')).toBeInTheDocument();
    // Пустой документ кнопку не включит: отправлять нечего.
    fireEvent.submit(form);
    expect(api.saveSet).not.toHaveBeenCalled();

    fireEvent.change(text, { target: { value: JSON.stringify(BODY) } });
    await userEvent.type(within(form).getByLabelText('Что и почему меняем'), 'Новая версия');
    await userEvent.click(within(form).getByRole('button', { name: 'Сохранить версию' }));
    expect(await within(form).findByRole('alert')).toHaveTextContent('Классификатора c-x нет в справочнике.');
  });
});
