import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { Rule } from '../api';
import { asApi, fakeApi, RULE, ruleViolation, type FakeApi } from '../test/fixtures';
import { RuleForm } from './RuleForm';

const VWAP_ONLY: Rule = {
  price_types: ['vwap'],
  dema_timeframe: null,
  dema_half_life: null,
  spread_threshold_bp: 50,
};

function renderForm(rule: Rule = VWAP_ONLY, api: FakeApi = fakeApi(), timeframes = [10, 60]) {
  const onDone = vi.fn();
  render(<RuleForm id="i-1" rule={rule} timeframes={timeframes} api={asApi(api)} onDone={onDone} />);
  return { api, onDone };
}

function order() {
  return within(screen.getByRole('list', { name: 'Типы цен по порядку' }))
    .getAllByRole('listitem')
    .map((item) => item.firstChild?.textContent?.trim());
}

async function save(comment = 'Неликвидная бумага: сглаживаем') {
  await userEvent.type(screen.getByLabelText('Комментарий — почему меняем'), comment);
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить правило' }));
}

describe('RuleForm', () => {
  it('adds DEMA as the second price for an illiquid bond', async () => {
    const { api, onDone } = renderForm();

    await userEvent.selectOptions(screen.getByLabelText('Тип цены'), 'dema');
    await userEvent.click(screen.getByRole('button', { name: 'Добавить' }));
    expect(order()).toEqual(['VWAP', 'DEMA']);
    expect(screen.getByText('Скрипт присылал бары: 10 мин, 60 мин.')).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('Таймфрейм баров, минут'), '10');
    await userEvent.type(screen.getByLabelText('Период полураспада, минут'), '60,5');
    await save();

    expect(api.setRule).toHaveBeenCalledWith('i-1', {
      price_types: ['vwap', 'dema'],
      dema_timeframe: 10,
      dema_half_life: '60.5',
      spread_threshold_bp: 50,
      comment: 'Неликвидная бумага: сглаживаем',
    });
    expect(onDone).toHaveBeenCalledOnce();
  });

  it('reorders and removes price types', async () => {
    const { api } = renderForm(RULE);

    await userEvent.click(screen.getByRole('button', { name: 'Ниже: VWAP' }));
    expect(order()).toEqual(['DEMA', 'VWAP']);
    await userEvent.click(screen.getByRole('button', { name: 'Выше: VWAP' }));
    expect(order()).toEqual(['VWAP', 'DEMA']);
    await userEvent.click(screen.getByRole('button', { name: 'Убрать: DEMA' }));
    expect(order()).toEqual(['VWAP']);
    expect(screen.getByRole('button', { name: 'Убрать: VWAP' })).toBeDisabled();
    // Параметры DEMA остались — их можно стереть, и DEMA уйдёт из правила.
    await userEvent.clear(screen.getByLabelText('Таймфрейм баров, минут'));
    expect(screen.queryByLabelText('Период полураспада, минут')).not.toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText('Порог разброса, бп'));
    await userEvent.type(screen.getByLabelText('Порог разброса, бп'), '30');
    await save('Только VWAP');

    expect(api.setRule).toHaveBeenCalledWith('i-1', {
      price_types: ['vwap'],
      dema_timeframe: null,
      dema_half_life: null,
      spread_threshold_bp: 30,
      comment: 'Только VWAP',
    });
  });

  it('offers every price type until all are in the rule', async () => {
    renderForm();

    for (let added = 1; added < 8; added += 1) {
      await userEvent.click(screen.getByRole('button', { name: 'Добавить' }));
    }

    expect(order()).toHaveLength(8);
    expect(screen.queryByRole('button', { name: 'Добавить' })).not.toBeInTheDocument();
  });

  it('says when no bars came yet', () => {
    renderForm(RULE, fakeApi(), []);

    expect(screen.getByText('Скрипт ещё не присылал бары по этой бумаге.')).toBeInTheDocument();
  });

  it('shows why the service refused', async () => {
    const api = fakeApi();
    api.setRule.mockRejectedValue(ruleViolation('Правило не изменилось.'));
    const { onDone } = renderForm(VWAP_ONLY, api);

    await save('Без изменений');

    expect(await screen.findByRole('alert')).toHaveTextContent('Правило не изменилось.');
    expect(onDone).not.toHaveBeenCalled();
  });
});
