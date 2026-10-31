import { LoadedView, useLoad } from '@am/ui';
import { useCallback } from 'react';
import { useSearchParams } from 'react-router';

import type { Context } from '../App';
import type { ResultDay, Results } from '../api';
import { FLAG_LABELS, formatDate, formatMoney, moscowToday, sumMoney } from '../format';

const PARTS = [
  ['revaluation', 'Переоценка'],
  ['realized', 'Реализованный'],
  ['coupon', 'Купонный доход'],
  ['fees', 'Комиссии'],
  ['expenses', 'Расходы'],
] as const;

/**
 * Финансовый результат за период (design/06): по дням — стоимость, приток
 * и части результата; по бумагам — те же части за весь период. Части в сумме
 * дают результат, а результат — изменение стоимости за вычетом притока.
 */
export function ResultsPage({ context }: { readonly context: Context }) {
  const [params, setParams] = useSearchParams();
  const fallback = context.portfolios.find((p) => p.is_fund) ?? context.portfolios[0];
  const portfolioId = params.get('portfolio') ?? fallback?.id ?? '';
  const till = params.get('till') ?? moscowToday();
  const first = params.get('from') ?? `${till.slice(0, 8)}01`;
  const load = useCallback(
    () => context.api.results(portfolioId, first, till),
    [context.api, portfolioId, first, till],
  );
  const [state] = useLoad(load);

  if (context.portfolios.length === 0) {
    return <p className="muted">Нет доступных портфелей: доступ выдаёт администратор.</p>;
  }
  const update = (changes: Record<string, string>) =>
    setParams({ portfolio: portfolioId, from: first, till, ...changes });
  return (
    <>
      <form className="inline" aria-label="Портфель и период" onSubmit={(e) => e.preventDefault()}>
        <label>
          Портфель
          <select value={portfolioId} onChange={(e) => update({ portfolio: e.target.value })}>
            {context.portfolios.map((p) => (
              <option key={p.id} value={p.id}>
                {p.code} — {p.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          С
          <input type="date" value={first} required onChange={(e) => e.target.value && update({ from: e.target.value })} />
        </label>
        <label>
          По
          <input type="date" value={till} required onChange={(e) => e.target.value && update({ till: e.target.value })} />
        </label>
      </form>
      <LoadedView state={state}>{(results) => <ResultsView results={results} />}</LoadedView>
    </>
  );
}

function ResultsView({ results }: { readonly results: Results }) {
  const { days, lines } = results;
  const incomplete = [...new Set(days.flatMap((d) => d.flags))];
  return (
    <>
      <p className="muted">Портфели: {results.portfolios.join(', ')}.</p>
      {results.waiting_since && (
        <p className="notice notice-warning">
          Пересчитывается с {formatDate(results.waiting_since)} — пока показан прежний расчёт.
        </p>
      )}
      {days.length === 0 ? (
        <p className="muted">Расчёта за период нет.</p>
      ) : (
        <>
          {incomplete.length > 0 && (
            <p className="notice notice-warning">
              Оценка неполная в днях со звёздочкой: {incomplete.map((f) => FLAG_LABELS[f]).join('; ')}.
            </p>
          )}
          <table className="data" aria-label="Результат по дням">
            <thead>
              <tr>
                <th>Дата</th>
                <th className="num">Стоимость, ₽</th>
                <th className="num">Приток, ₽</th>
                {PARTS.map(([, label]) => (
                  <th key={label} className="num">
                    {label}, ₽
                  </th>
                ))}
                <th className="num">Результат, ₽</th>
              </tr>
            </thead>
            <tbody>
              {days.map((d) => (
                <DayRow key={d.date} day={d} />
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th>Итого</th>
                <td className="num">{formatMoney(days[days.length - 1]!.value)}</td>
                <td className="num">{formatMoney(sumMoney(days.map((d) => d.flows)))}</td>
                {PARTS.map(([name]) => (
                  <td key={name} className="num">
                    {formatMoney(sumMoney(days.map((d) => d[name])))}
                  </td>
                ))}
                <td className="num">
                  <strong>{formatMoney(sumMoney(days.map((d) => d.total)))}</strong>
                </td>
              </tr>
            </tfoot>
          </table>
        </>
      )}
      {lines.length > 0 && (
        <table className="data" aria-label="Результат по бумагам">
          <thead>
            <tr>
              <th>Бумага</th>
              {PARTS.slice(0, 4).map(([, label]) => (
                <th key={label} className="num">
                  {label}, ₽
                </th>
              ))}
              <th className="num">Результат, ₽</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => (
              <tr key={line.instrument_id}>
                <td>
                  <span className="code">{line.secid}</span> — {line.short_name}
                </td>
                {PARTS.slice(0, 4).map(([name]) => (
                  <td key={name} className="num">
                    {formatMoney(line[name as 'revaluation' | 'realized' | 'coupon' | 'fees'])}
                  </td>
                ))}
                <td className="num">{formatMoney(line.total)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

function DayRow({ day }: { readonly day: ResultDay }) {
  return (
    <tr>
      <td>
        {formatDate(day.date)}
        {day.flags.length > 0 && <span className="danger"> *</span>}
      </td>
      <td className="num">{formatMoney(day.value)}</td>
      <td className="num">{formatMoney(day.flows)}</td>
      {PARTS.map(([name]) => (
        <td key={name} className="num">
          {formatMoney(day[name])}
        </td>
      ))}
      <td className="num">{formatMoney(day.total)}</td>
    </tr>
  );
}
