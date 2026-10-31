import { useSearchParams } from 'react-router';

import type { Portfolio } from '../api';
import { moscowToday } from '../format';

/**
 * Портфель и дата — в адресе страницы: их видно в ссылке, и они общие
 * для списка сделок и позиций. По умолчанию — первый фонд и сегодня.
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

export function SelectionForm({
  portfolios,
  portfolioId,
  date,
  onChange,
}: {
  readonly portfolios: readonly Portfolio[];
  readonly portfolioId: string;
  readonly date: string;
  readonly onChange: (changes: { portfolio?: string; date?: string }) => void;
}) {
  return (
    <form className="inline" aria-label="Портфель и дата" onSubmit={(e) => e.preventDefault()}>
      <label>
        Портфель
        <select value={portfolioId} onChange={(e) => onChange({ portfolio: e.target.value })}>
          {portfolios.map((p) => (
            <option key={p.id} value={p.id}>
              {p.code} — {p.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        Дата
        <input
          type="date"
          value={date}
          required
          onChange={(e) => e.target.value && onChange({ date: e.target.value })}
        />
      </label>
    </form>
  );
}
