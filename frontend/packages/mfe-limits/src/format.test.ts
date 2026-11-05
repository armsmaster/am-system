import { describe, expect, it } from 'vitest';

import {
  breachLimit,
  breachValue,
  checkText,
  conditionText,
  filterText,
  formatDate,
  formatNumber,
  formatTime,
  monthText,
  names,
  plain,
  ratingText,
  reportMonth,
  thresholdText,
  todayInMoscow,
  usageText,
  valueText,
  years,
} from './format';
import { BODY, breach, check, CLASSIFIERS, INSTRUMENTS, ISSUERS } from './test/fixtures';

describe('breaches', () => {
  it('names the limit and tells the value against the bound', () => {
    expect(breachLimit(breach())).toBe('Л4, на эмитента');
    expect(breachLimit(breach({ limit_code: 'Л1', label: '' }))).toBe('Л1');
    expect(breachValue(breach())).toBe('2,68% при лимите 2,5%');
    expect(breachValue(breach({ check_type: 'metric', value: '4.2', maximum: '4' }))).toBe('4,20 при лимите 4');
    expect(breachValue(breach({ check_type: 'metric', value: '0.9', maximum: null, minimum: '1' }))).toBe(
      '0,90 при минимуме 1',
    );
    expect(breachValue(breach({ check_type: 'metric', value: '0.9', maximum: null }))).toBe('0,90 при минимуме 0');
    const rule = breach({ check_type: 'eligible', value: null, maximum: null });
    expect(breachValue({ ...rule, reason: 'позиция не подходит ни под одно правило' })).toBe(
      'позиция не подходит ни под одно правило',
    );
    expect(breachValue(rule)).toBe('—');
  });

  it('names the month of the report', () => {
    expect(monthText('2026-11')).toBe('ноябрь 2026');
    expect(reportMonth('2026-11-17')).toBe('2026-11');
    // 1-го числа прошедших дней месяца ещё нет — отчёт за прошлый месяц.
    expect(reportMonth('2026-12-01')).toBe('2026-11');
    expect(reportMonth('2027-01-01')).toBe('2026-12');
  });
});

const N = names(CLASSIFIERS, ISSUERS, INSTRUMENTS);

describe('numbers and dates', () => {
  it('formats the Russian way', () => {
    expect(formatNumber('2800000000.00')).toBe('2 800 000 000,00');
    expect(formatNumber('-5.5', 1)).toBe('−5,5');
    expect(formatNumber('7', 0)).toBe('7');
    expect(formatNumber(null)).toBe('—');
    expect(plain('2.5')).toBe('2,5');
    expect(formatDate('2026-11-10')).toBe('10.11.2026');
    expect(formatTime('2026-11-02T09:00:00Z')).toBe('02.11.2026, 12:00');
    expect(todayInMoscow(new Date('2026-11-16T22:30:00Z'))).toBe('2026-11-17');
    expect(['2.5', '0', '1', '21', '2', '24', '5', '11', '12', '14'].map(years)).toEqual([
      'года',
      'лет',
      'год',
      'год',
      'года',
      'года',
      'лет',
      'лет',
      'лет',
      'лет',
    ]);
  });
});

describe('usage', () => {
  it.each([
    [check({ type: 'metric', metric: 'leverage', value: '2.8000', groups: [] }), '2,80', 'не более 10'],
    [check({ type: 'metric', metric: 'duration', value: '0.4319', maximum: '2.5', groups: [] }), '0,43 года', 'не более 2,5 года'],
    [check({ type: 'metric', metric: 'duration', value: null, maximum: null, minimum: '0', groups: [] }), '—', 'не менее 0 лет'],
    [check(), '9,64%, Эмитент А', 'не более 10%'],
    [check({ groups: [{ key: 'all', name: 'все', value: '51.6', status: 'ok' }], value: '51.6071' }), '51,61%', 'не более 10%'],
    [check({ type: 'eligible', value: '0.0000', maximum: null, groups: [] }), 'нарушений нет', '—'],
    [check({ type: 'purchase', value: '2.0000', maximum: null, groups: [] }), 'нарушений: 2', '—'],
  ])('%#: value and threshold', (found, value, threshold) => {
    expect(valueText(found)).toBe(value);
    expect(thresholdText(found)).toBe(threshold);
  });

  it('rounds the used share of a limit', () => {
    expect(usageText('96.43')).toBe('96%');
    expect(usageText(null)).toBe('—');
  });
});

describe('the set in words', () => {
  it('names attributes and values from the reference data', () => {
    expect(conditionText({ attribute: 'iss_type', values: ['ofz_bond'], negate: true }, N)).toBe('тип ISS: не ОФЗ');
    expect(conditionText({ attribute: 'class:c-seniority', values: ['v-senior', 'v-x'] }, N)).toBe(
      '«Очерёдность»: старший долг, v-x',
    );
    expect(conditionText({ attribute: 'class:c-lost', values: ['v-1'] }, N)).toBe(
      'классификатор, которого нет в справочнике: v-1',
    );
    expect(conditionText({ attribute: 'issuer', values: ['e-veb', 'e-x'] }, N)).toBe('эмитент: ВЭБ.РФ, e-x');
    expect(conditionText({ attribute: 'instrument', values: ['i-lqdt', 'i-x'] }, N)).toBe('бумага: LQDT, i-x');
    expect(conditionText({ attribute: 'rating_group', values: ['AA', 'none'] }, N)).toBe(
      'рейтинговая группа: AA, группы нет',
    );
    expect(conditionText({ attribute: 'currency', values: ['RUB'] }, N)).toBe('валюта: RUB');
    expect(conditionText({ attribute: 'kind', values: ['bond', 'odd'] }, N)).toBe('вид: облигация, odd');
    expect(conditionText({ attribute: 'colour', values: ['red'] }, N)).toBe('colour: red');
    expect(filterText(undefined, N)).toBe('все бумаги');
  });

  it('describes every kind of check', () => {
    const [leverage, share, eligible, purchase] = BODY.limits.map((l) => l.checks[0]!);

    expect(checkText(leverage!, N)).toBe('Плечо — активы / СЧА: не более 4,0.');
    expect(checkText({ type: 'metric', metric: 'duration', min: '0' }, N)).toBe(
      'Модифицированная дюрация к активам: не менее 0 лет.',
    );
    expect(checkText(share!, N)).toBe(
      'Доля в активах на одного эмитента — не более 10%. Бумаги: тип ISS: не ОФЗ; «Очерёдность»: старший долг; ' +
        'рейтинговая группа: AAA, AA+, AA. Не применяется к эмитентам: ВЭБ.РФ.',
    );
    expect(checkText({ type: 'share', max: '20', exclude_issuers: ['e-x'] }, N)).toBe(
      'Доля в активах всех бумаг проверки вместе — не более 20%. Бумаги: все бумаги. Не применяется к эмитентам: e-x.',
    );
    expect(checkText({ type: 'share', max: '2.5', group: 'instrument' }, N)).toBe(
      'Доля в активах на один выпуск — не более 2,5%. Бумаги: все бумаги.',
    );
    expect(checkText(eligible!, N)).toBe(
      'Каждая позиция и сделка дня подходит под одно из правил: «паи БПИФ» — бумага: LQDT | ' +
        '«ОФЗ» — тип ISS: ОФЗ; короткие позиции разрешены.',
    );
    expect(checkText(purchase!, N)).toBe(
      'При покупке бумаг (вид: облигация) на дату сделки: «Принимается НКЦ в обеспечение»: да.',
    );
  });

  it('describes the rating rule of the set', () => {
    expect(ratingText(BODY.settings, N)).toEqual([
      'Агентства: АКРА, Эксперт РА; из нескольких рейтингов — наивысший.',
      'Рейтинговые группы: AAA → AAA, AA+ → AA+, AA → AA, AA- → AA; остальные ступени — группы нет.',
      '«Очерёдность»: субординированная → рейтинг эмитента.',
      'все бумаги → рейтинг выпуска, а без него — эмитента.',
      'Предупреждение — с 90% лимита.',
    ]);
    const lowest = { ratings: { ...BODY.settings.ratings, agencies: ['moodys'], pick: 'lowest' as const, rules: [{ source: 'instrument' as const }] } };
    expect(ratingText(lowest, N)).toEqual([
      'Агентства: moodys; из нескольких рейтингов — наинизший.',
      'Рейтинговые группы: AAA → AAA, AA+ → AA+, AA → AA, AA- → AA; остальные ступени — группы нет.',
      'все бумаги → рейтинг выпуска.',
      'Предупреждение — с 90% лимита.',
    ]);
  });
});
