import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { Params } from '../api';
import { floaterParams, params, version } from '../test/fixtures';
import {
  AmortizationsTable,
  couponSummary,
  CouponsTable,
  nextCoupon,
  OffersTable,
  VersionFacts,
} from './VersionDetails';

const TODAY = '2026-11-13';

function facts() {
  const terms = screen.getAllByRole('term').map((dt) => dt.textContent);
  const values = screen.getAllByRole('definition').map((dd) => dd.textContent);
  return Object.fromEntries(terms.map((term, i) => [term, values[i]]));
}

function rows(name: string) {
  return within(screen.getByRole('table', { name }))
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell').map((c) => c.textContent));
}

describe('VersionFacts', () => {
  it('shows every parameter of a bond and marks pinned ones', () => {
    render(
      <VersionFacts
        version={version(2, {
          valid_from: '2026-10-01',
          source: 'manual',
          author: 'ops',
          comment: 'Дата погашения по проспекту',
          pinned: ['maturity_date'],
        })}
        kind="bond"
      />,
    );

    expect(screen.getByRole('heading', { name: 'Версия 2: действует с 01.10.2026' })).toBeInTheDocument();
    expect(screen.getByText('вручную, ops — Дата погашения по проспекту')).toBeInTheDocument();
    expect(facts()).toEqual({
      Название: 'ОФЗ-ПД 26238 15/05/2041',
      'Краткое название': 'ОФЗ 26238',
      Эмитент: 'Министерство финансов Российской Федерации',
      'Режим торгов': 'TQOB',
      Валюта: 'RUB',
      'Начальный номинал': '1 000',
      'Дата выпуска': '16.06.2021',
      'Дата погашения': '15.05.2041 закреплено',
      'Тип купона': 'фиксированный',
      'Купонов в год': '2',
      'Индексация номинала': 'нет',
    });
  });

  it.each([
    ['not filled in yet', floaterParams(), 'не заполнены'],
    [
      'a margin over the key rate',
      floaterParams({ floater: { rate: 'key_rate', margin_bp: '130', capitalized: false } }),
      'ключевая ставка ЦБ +130 бп, без капитализации',
    ],
    [
      'a negative capitalized margin over RUONIA',
      floaterParams({ floater: { rate: 'ruonia', margin_bp: '-15.5', capitalized: true } }),
      'RUONIA -15,5 бп, капитализируется',
    ],
    [
      'terms left from a floater that became fixed',
      params({ floater: { rate: 'rusfar', margin_bp: '50', capitalized: false } }),
      'RUSFAR +50 бп, без капитализации',
    ],
  ])('shows floater terms: %s', (_name, p, expected) => {
    render(<VersionFacts version={version(1, { params: p })} kind="bond" />);

    expect(facts()['Параметры флоатера']).toBe(expected);
  });

  it.each([
    ['a known rate', { benchmark: 'RREFKEYR', rate: 'key_rate', margin_bp: '130.00' }, 'ключевая ставка ЦБ +130 бп'],
    ['an unknown ISS code', { benchmark: 'MOSPRIME3M', rate: 'other', margin_bp: '-20.50' }, 'MOSPRIME3M -20,5 бп'],
    ['no margin', { benchmark: 'RUONIA', rate: 'ruonia', margin_bp: null }, 'RUONIA'],
    ['a whole margin', { benchmark: 'RUONIA', rate: 'ruonia', margin_bp: '100' }, 'RUONIA +100 бп'],
  ] as const)('shows the floater terms published by ISS: %s', (_name, iss, expected) => {
    render(<VersionFacts version={version(1, { params: floaterParams({ iss_floater: iss }) })} kind="bond" />);

    expect(facts()['Ставка и маржа по ISS']).toBe(expected);
  });

  it('shows a fund share without bond terms', () => {
    render(
      <VersionFacts
        version={version(1, { comment: null, params: params({ issuer: null, coupon_kind: null }) })}
        kind="fund_share"
      />,
    );

    expect(screen.getByText('MOEX ISS, trader')).toBeInTheDocument();
    expect(facts()).toMatchObject({ Эмитент: '—' });
    expect(screen.queryByText('Тип купона')).not.toBeInTheDocument();
    expect(screen.queryByText('Начальный номинал')).not.toBeInTheDocument();
  });

  it('shows a dash for an unknown coupon frequency', () => {
    render(<VersionFacts version={version(1, { params: params({ coupon_frequency: null }) })} kind="bond" />);

    expect(facts()['Купонов в год']).toBe('—');
  });
});

describe('schedules', () => {
  it('highlights the next coupon and shows the past ones on request', async () => {
    const { rerender } = render(<CouponsTable params={params()} today={TODAY} />);

    expect(rows('Купоны')).toEqual([
      ['25.11.2026 ближайшийфиксация 24.11.2026', '182D', '7,1%', '35,4'],
      ['26.05.2027', '182D', '—', 'не объявлен'],
    ]);
    expect(screen.getByRole('columnheader', { name: 'Сумма, ₽' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /прошедшие/ })).not.toBeInTheDocument();

    // Первый купон прошёл: он скрыт, пока не попросят.
    rerender(<CouponsTable params={params()} today="2026-12-01" />);
    expect(rows('Купоны').map((row) => row[0])).toEqual(['26.05.2027 ближайший']);
    await userEvent.click(screen.getByRole('button', { name: 'Показать прошедшие купоны: 1' }));
    const [, first, second] = within(screen.getByRole('table', { name: 'Купоны' })).getAllByRole('row');
    expect(first).toHaveClass('past');
    expect(second).toHaveClass('next');
    await userEvent.click(screen.getByRole('button', { name: 'Скрыть прошедшие купоны' }));
    expect(rows('Купоны')).toHaveLength(1);
  });

  it('says when no coupons are left', () => {
    render(<CouponsTable params={params({ currency: 'USD' })} today="2030-01-01" />);

    expect(screen.getByText('Купонов впереди нет.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Показать прошедшие купоны: 2' })).toBeInTheDocument();
  });

  it('writes a monthly coupon in months and days', () => {
    const monthly = params({
      coupons: [{ date: '2026-12-12', start_date: '2026-11-12', record_date: null, rate: null, amount: null }],
    });
    render(<CouponsTable params={monthly} today={TODAY} />);

    expect(rows('Купоны')[0]![1]).toBe('1M · 30 дн.');
  });

  it('explains why a linker has no future payment amounts', () => {
    const linker = params({
      nominal_indexation: 'ruonia',
      coupons: [{ date: '2026-11-19', start_date: null, record_date: null, rate: '1.85', amount: null }],
    });
    render(<CouponsTable params={linker} today={TODAY} />);


    expect(screen.getByText(/сумма купона и погашения зависит от номинала в день выплаты/)).toBeInTheDocument();
    expect(rows('Купоны')).toEqual([['19.11.2026 ближайший', '—', '1,85%', 'не объявлен']]);
  });

  it('shows redemptions and offers', () => {
    const p: Params = params({
      offers: [
        {
          date: '2028-05-17',
          kind: 'put',
          window_start: '2028-05-03',
          window_end: '2028-05-10',
          price: '100',
          iss_type: 'Оферта',
        },
        { date: '2030-05-15', kind: 'call', window_start: null, window_end: null, price: null, iss_type: null },
      ],
      amortizations: [
        { date: '2040-05-16', percent: '50', amount: '500', is_maturity: false },
        { date: '2041-05-15', percent: '50', amount: '500', is_maturity: true },
      ],
    });
    render(
      <>
        <AmortizationsTable params={p} />
        <OffersTable params={p} />
      </>,
    );

    expect(rows('Погашение номинала')).toEqual([
      ['16.05.2040', '50', '500'],
      ['15.05.2041 погашение', '50', '500'],
    ]);
    expect(rows('Оферты')).toEqual([
      ['17.05.2028приём заявок 03.05.2028 – 10.05.2028', 'put — выкуп у держателейОферта', '100'],
      ['15.05.2030приём заявок не указан', 'call — погашение эмитентом', '—'],
    ]);
  });

  it('says when a schedule is empty', () => {
    const empty = params({ coupons: [], amortizations: [], offers: [] });
    render(
      <>
        <CouponsTable params={empty} today={TODAY} />
        <AmortizationsTable params={empty} />
        <OffersTable params={empty} />
      </>,
    );

    expect(screen.getByText('Купонов нет.')).toBeInTheDocument();
    expect(screen.getByText('Графика погашения нет.')).toBeInTheDocument();
    expect(screen.getByText('Оферт нет.')).toBeInTheDocument();
  });
});

describe('couponSummary', () => {
  const monthly = [
    { date: '2026-12-12', start_date: '2026-11-12', record_date: null, rate: null, amount: null },
    { date: '2027-01-12', start_date: '2026-12-12', record_date: null, rate: null, amount: null },
  ];

  it.each([
    ['a fixed coupon: the next known rate', params(), TODAY, '7,1% · 182D'],
    ['a fixed coupon after the last known rate', params(), '2027-01-01', '7,1% · 182D'],
    [
      'a floater with terms from operations',
      floaterParams({ floater: { rate: 'key_rate', margin_bp: '130', capitalized: false }, coupons: monthly }),
      TODAY,
      'КС + 130 бп · 1M',
    ],
    [
      'a floater with terms from ISS',
      floaterParams({ iss_floater: { benchmark: 'RUONIA', rate: 'ruonia', margin_bp: '175.00' }, coupons: [] }),
      TODAY,
      'RUONIA + 175 бп',
    ],
    ['a floater without terms', floaterParams({ coupons: [] }), TODAY, 'флоатер'],
    ['no known rate', params({ coupons: monthly }), TODAY, '— · 1M'],
  ])('%s', (_name, p, today, expected) => {
    expect(couponSummary(p, today)).toBe(expected);
  });

  it('finds no next coupon after the last one', () => {
    expect(nextCoupon(params().coupons, '2030-01-01')).toBeNull();
  });
});
