import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { Params } from '../api';
import { floaterParams, params, version } from '../test/fixtures';
import { VersionDetails } from './VersionDetails';

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

const nbsp = (text: string) => text.replaceAll(' ', ' ');

describe('VersionDetails', () => {
  it('shows every parameter of a bond and marks pinned ones', () => {
    render(
      <VersionDetails
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
      Номинал: nbsp('1 000'),
      'Начальный номинал': nbsp('1 000'),
      'Дата выпуска': '16.06.2021',
      'Дата погашения': '15.05.2041 закреплено',
      'Тип купона': 'фиксированный',
      'Купонов в год': '2',
      'Индексация номинала': 'нет',
    });
  });

  it('shows coupon, redemption and offer schedules', () => {
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
    render(<VersionDetails version={version(1, { params: p })} kind="bond" />);

    expect(rows('Купоны')).toEqual([
      ['25.11.2026', '27.05.2026', '24.11.2026', '7,1', '35,4'],
      ['26.05.2027', '25.11.2026', '—', '—', '—'],
    ]);
    expect(rows('Амортизации')).toEqual([
      ['16.05.2040', '50', '500', ''],
      ['15.05.2041', '50', '500', 'погашение'],
    ]);
    expect(rows('Оферты')).toEqual([
      ['17.05.2028', 'put — выкуп по требованию держателяОферта', '03.05.2028 – 10.05.2028', '100'],
      ['15.05.2030', 'call — досрочное погашение эмитентом', '—', '—'],
    ]);
  });

  it('says when a schedule is empty', () => {
    render(
      <VersionDetails
        version={version(1, { params: params({ coupons: [], amortizations: [], offers: [] }) })}
        kind="bond"
      />,
    );

    expect(screen.getByText('Купонов нет.')).toBeInTheDocument();
    expect(screen.getByText('Графика погашения нет.')).toBeInTheDocument();
    expect(screen.getByText('Оферт нет.')).toBeInTheDocument();
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
    render(<VersionDetails version={version(1, { params: p })} kind="bond" />);

    expect(facts()['Параметры флоатера']).toBe(expected);
  });

  it('shows a fund share without bond terms and schedules', () => {
    render(
      <VersionDetails
        version={version(1, {
          comment: null,
          params: params({ issuer: null, face_value: null, coupon_kind: null }),
        })}
        kind="fund_share"
      />,
    );

    expect(screen.getByText('MOEX ISS, trader')).toBeInTheDocument();
    expect(facts()).toMatchObject({ Эмитент: '—', Номинал: '—' });
    expect(screen.queryByText('Тип купона')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Купоны' })).not.toBeInTheDocument();
  });

  it('shows a dash for an unknown coupon frequency', () => {
    render(<VersionDetails version={version(1, { params: params({ coupon_frequency: null }) })} kind="bond" />);

    expect(facts()['Купонов в год']).toBe('—');
  });
});
