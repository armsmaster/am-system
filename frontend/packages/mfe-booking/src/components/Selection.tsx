import { useSearchParams } from 'react-router';

import type { Portfolio } from '../api';
import { moscowToday } from '../format';

/**
 * Портфель и дата — в адресе страницы: их видно в ссылке, и они общие
 * для позиций и операций фонда. По умолчанию — первый фонд и сегодня.
 */
export function useSelection(portfolios: readonly Portfolio[]) {
  const [params, setParams] = useSearchParams();
  const fallback = portfolios.find((p) => p.is_fund) ?? portfolios[0];
  const portfolioId = params.get('portfolio') ?? fallback?.id ?? '';
  const date = params.get('date') ?? moscowToday();

  function update(changes: { portfolio?: string; date?: string }) {
    setParams({ portfolio: changes.portfolio ?? portfolioId, date: changes.date ?? date });
  }

  return { portfolioId, date, update };
}
