import { describe, expect, it } from 'vitest';

import {
  decimalInput,
  describeDifference,
  formatMoney,
  formatQuantity,
  formatTime,
  lineStatus,
  moscowToday,
  movementTitle,
  previousDay,
} from './format';

describe('format', () => {
  it.each([
    [{ field: 'fee', system: '0', statement: '2800.05' }, 'комиссия: 0,00 в системе, 2 800,05 в выписке'],
    [{ field: 'quantity', system: '20000', statement: '25000' }, 'количество: 20 000 в системе, 25 000 в выписке'],
    [{ field: 'settle_date', system: '2026-11-30', statement: '2026-12-01' }, 'дата расчётов: 30.11.2026 в системе, 01.12.2026 в выписке'],
    [{ field: 'external_id', system: null, statement: '1000000001' }, 'номер сделки: — в системе, 1000000001 в выписке'],
    [{ field: 'side', system: 'buy', statement: 'sell' }, 'направление: покупка в системе, продажа в выписке'],
    [{ field: 'kind', system: 'outright', statement: 'repo' }, 'вид сделки: покупка или продажа в системе, РЕПО в выписке'],
    [{ field: 'price', system: '52.5', statement: '52.6' }, 'цена: 52,5 в системе, 52,6 в выписке'],
    [{ field: 'mystery', system: 'a', statement: 'b' }, 'mystery: a в системе, b в выписке'],
  ])('describes %o', (difference, text) => {
    expect(describeDifference(difference)).toBe(text);
  });

  it('names a missing line by who gave the statement', () => {
    expect([lineStatus('not_in_statement', 'broker'), lineStatus('not_in_statement', 'bank')]).toEqual([
      'нет у брокера',
      'нет в банке',
    ]);
  });

  it('formats money, quantities, days and input', () => {
    expect([formatMoney(null), formatMoney('-885000'), formatMoney('2800.05')]).toEqual(['—', '−885 000,00', '2 800,05']);
    expect(formatQuantity(50000)).toBe('50 000');
    expect(previousDay('2026-12-01')).toBe('2026-11-30');
    expect(moscowToday(new Date('2026-12-02T22:30:00Z'))).toBe('2026-12-03');
    expect(formatTime('2026-12-02T20:15:00Z')).toBe('02.12, 23:15');
    expect(decimalInput(' −20 550,5 ')).toBe('-20550.5');
    expect(movementTitle('transfer', null, null)).toBe('перевод между счетами фонда');
  });
});
