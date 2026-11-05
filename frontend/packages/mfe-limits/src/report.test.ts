import { describe, expect, it } from 'vitest';

import { reportCsv } from './report';
import { breach, fundDay, report } from './test/fixtures';

describe('reportCsv', () => {
  it('lays out the usage and the breaches for Excel', () => {
    const text = reportCsv(
      report({
        breaches: [
          breach({
            state: 'eliminated',
            eliminated_on: '2026-11-17',
            notes: [{ by: 'risk', at: 'x', text: 'Рейтинг; понижен "Эксперт РА"' }],
            extensions: [{ deadline: '2027-01-29', decision: '№ 5', comment: 'Рынок', by: 'risk', at: 'x' }],
          }),
        ],
      }),
    );
    const lines = text.split('\r\n');

    // BOM — чтобы Excel узнал UTF-8.
    expect(text.startsWith('﻿')).toBe(true);
    expect(lines.slice(0, 5)).toEqual([
      '﻿Использование лимитов: DOHOD2 — Доход 2, ноябрь 2026',
      '',
      'Использование на 16.11.2026',
      'Лимит;Проверка;Значение;Порог;Использовано;Статус',
      'Л1 · Плечо;;2,80;не более 4,0;70%;в пределах',
    ]);
    expect(lines.at(-2)).toBe(
      'Л4, на эмитента;Эмитент Г;2,68% при лимите 2,5%;12.11.2026;12.11.2026;пассивное;10.12.2026;устранено;' +
        '17.11.2026;до 29.01.2027, № 5;"risk: Рейтинг; понижен ""Эксперт РА"""',
    );
  });

  it('says when there is nothing to show', () => {
    const empty = (day: Parameters<typeof fundDay>[0]) =>
      reportCsv(report({ day: fundDay({ checks: [], ...day }), breaches: [] }));

    expect(empty({ limit_set: null })).toContain('Набора лимитов на этот день нет.');
    expect(empty({ calculated: false })).toContain('Расчёта фонда за этот день нет.');
    expect(empty({})).toContain('Нарушений за месяц не было.');
  });
});
