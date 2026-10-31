import { ErrorNote, LoadedView, useLoad } from '@am/ui';
import { useCallback, useState, type FormEvent } from 'react';

import type { Analytics, HorizonYield, MarketdataApi, Rate, Spread, WhatIf } from '../api';
import { formatDate, formatNumber, formatPrice, PRICE_LABELS, sourceLabel } from '../format';
import { REFRESH_MS, useEvery } from '../refresh';

const HORIZON_LABELS = { offer: 'к оферте', maturity: 'к погашению' } as const;

const BASIS_LABELS: Readonly<Record<Analytics['price']['basis'], string>> = {
  valuation: 'цена для оценки',
  price: 'что если: введённая цена',
  yield: 'что если: цена по введённой доходности',
  spread: 'что если: цена по введённому спреду',
};

const RATE_SHORT: Readonly<Record<NonNullable<Spread['rate']>, string>> = {
  key_rate: 'КС',
  ruonia: 'RUONIA',
  rusfar: 'RUSFAR',
};

const TITLES: Readonly<Record<Analytics['type'], string>> = {
  fixed: 'Доходность и риск',
  floater: 'Спред над фондированием',
  linker: 'Спред над фондированием',
};

type Input = keyof WhatIf;

const INPUT_LABELS: Readonly<Record<Input, string>> = {
  price: 'Чистая цена, %',
  yield: 'Доходность, %',
  spread: 'Спред, бп',
};

/**
 * Расчёт по облигации на момент цены (design/04): доходность и метрики
 * или вменённый спред над фондированием с разложением; «что если».
 */
export function AnalyticsBlock({ api, id }: { readonly api: MarketdataApi; readonly id: string }) {
  const [whatIf, setWhatIf] = useState<WhatIf>({});
  const load = useCallback(() => api.analytics(id, whatIf), [api, id, whatIf]);
  const [state, reload] = useLoad(load);
  const active = Object.keys(whatIf).length > 0;
  // По цене для оценки расчёт следует за ценой; «что если» не обновляем.
  useEvery(reload, REFRESH_MS, !active);
  const type = state.status === 'ready' ? state.data.type : null;

  return (
    <section aria-label="Расчёт по облигации" className="details">
      <h3>{type === null ? 'Расчёт по облигации' : TITLES[type]}</h3>
      <LoadedView state={state}>
        {(found) => (
          <>
            <PriceLine found={found} />
            {found.spread === null ? (
              <YieldsTable yields={found.yields} />
            ) : (
              <SpreadView spread={found.spread} type={found.type} />
            )}
          </>
        )}
      </LoadedView>
      <WhatIfForm type={type} active={active} onApply={setWhatIf} />
    </section>
  );
}

function PriceLine({ found }: { readonly found: Analytics }) {
  const price = found.price;
  const valuation = price.valuation;
  return (
    <p className="muted">
      Чистая цена {formatNumber(price.clean, 4)}% ({BASIS_LABELS[price.basis]}
      {valuation !== null && `: ${PRICE_LABELS[valuation.type]}, ${sourceLabel(valuation.source)}`}).
      Дата расчётов — {formatDate(found.settlement_date)}: номинал {formatPrice(price.face_value)} ₽,
      НКД {formatNumber(price.accrued, 2)} ₽, грязная цена {formatNumber(price.dirty, 2)} ₽.
    </p>
  );
}

function YieldsTable({ yields }: { readonly yields: readonly HorizonYield[] }) {
  return (
    <table className="data" aria-label="Доходность и метрики">
      <thead>
        <tr>
          <th>Горизонт</th>
          <th className="num">Доходность, %</th>
          <th className="num">Дюрация, лет</th>
          <th className="num">дней</th>
          <th className="num">Мод. дюрация</th>
          <th className="num">DV01, ₽</th>
          <th className="num">Выпуклость</th>
        </tr>
      </thead>
      <tbody>
        {yields.map((item) => (
          <tr key={item.horizon.kind}>
            <td>
              {HORIZON_LABELS[item.horizon.kind]} {formatDate(item.horizon.date)}
            </td>
            {item.problem === null ? (
              <>
                <td className="num">{formatNumber(item.yield_percent, 2)}</td>
                <td className="num">{formatNumber(item.macaulay_years, 2)}</td>
                <td className="num">{formatNumber(item.macaulay_days, 0)}</td>
                <td className="num">{formatNumber(item.modified_duration, 2)}</td>
                <td className="num">{formatNumber(item.dv01, 4)}</td>
                <td className="num">{formatNumber(item.convexity, 2)}</td>
              </>
            ) : (
              <td colSpan={6} className="muted">
                {item.problem.message}
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function rateLine(rate: Rate): string {
  if (rate.date === null) {
    return `${rate.label} — нет`;
  }
  return `${rate.label} ${formatNumber(rate.value, 2)}% на ${formatDate(rate.date)}, ${sourceLabel(rate.source)}`;
}

function SpreadView({ spread, type }: { readonly spread: Spread; readonly type: Analytics['type'] }) {
  const parts = spread.breakdown;
  const terms =
    spread.rate === null
      ? 'линкер: номинал растёт с RUONIA'
      : `${RATE_SHORT[spread.rate]} + ${formatNumber(spread.margin_bp, 0)} бп, ${
          spread.capitalized ? 'с капитализацией' : 'без капитализации'
        }`;
  const rows: readonly (readonly [string, string])[] = [
    ['Базис ставки', parts.basis_bp],
    ['Капитализация', parts.capitalization_bp],
    [type === 'linker' ? 'Купон' : 'Маржа', parts.margin_bp],
    ['Прочее', parts.other_bp],
    ['Поправка на цену', parts.price_bp],
  ];
  return (
    <>
      <p className="price">{formatNumber(spread.spread_bp, 1)} бп</p>
      <p className="muted">
        {terms}; {HORIZON_LABELS[spread.horizon.kind]} {formatDate(spread.horizon.date)}.
        Доходность при плоском прогнозе ставки — {formatNumber(spread.yield_percent, 2)}%.
      </p>
      <p className="muted">
        Фондирование: {rateLine(spread.funding)} → {formatNumber(spread.funding_percent, 2)}%
        эффективных годовых.
        {spread.index !== null && spread.index.rate !== 'ruonia' && ` Индекс: ${rateLine(spread.index)}.`}
      </p>
      <table className="data" aria-label="Разложение спреда">
        <tbody>
          {rows.map(([label, value]) => (
            <tr key={label}>
              <td>{label}</td>
              <td className="num">{formatNumber(value, 1)} бп</td>
            </tr>
          ))}
          <tr>
            <th>Спред</th>
            <th className="num">{formatNumber(spread.spread_bp, 1)} бп</th>
          </tr>
        </tbody>
      </table>
      <p className="muted">
        Спред-дюрация {formatNumber(spread.spread_duration, 2)} года, DV01 по спреду{' '}
        {formatNumber(spread.spread_dv01, 4)} ₽ на бумагу.
      </p>
    </>
  );
}

function WhatIfForm({
  type,
  active,
  onApply,
}: {
  /** null — расчёт не удался, тип бумаги неизвестен: предлагаем всё. */
  readonly type: Analytics['type'] | null;
  readonly active: boolean;
  readonly onApply: (whatIf: WhatIf) => void;
}) {
  const inputs: readonly Input[] =
    type === null ? ['price', 'yield', 'spread'] : type === 'fixed' ? ['price', 'yield'] : ['price', 'spread'];
  const [input, setInput] = useState<Input>('price');
  const [value, setValue] = useState('');
  const [error, setError] = useState<string | null>(null);

  function submit(event: FormEvent) {
    event.preventDefault();
    const number = value.trim().replace(',', '.');
    if (!/^-?\d+(\.\d+)?$/.test(number)) {
      setError('Введите число, например 99,50.');
      return;
    }
    setError(null);
    onApply({ [input]: number });
  }

  return (
    <form className="inline" onSubmit={submit} aria-label="Что если">
      <label>
        Что если
        <select value={input} onChange={(e) => setInput(e.target.value as Input)}>
          {inputs.map((name) => (
            <option key={name} value={name}>
              {INPUT_LABELS[name]}
            </option>
          ))}
        </select>
      </label>
      <label>
        Значение
        <input inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} />
      </label>
      <button type="submit">Посчитать</button>
      {active && (
        <button type="button" onClick={() => onApply({})}>
          Вернуть цену для оценки
        </button>
      )}
      <ErrorNote message={error} />
    </form>
  );
}
