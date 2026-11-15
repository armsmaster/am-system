import { ErrorNote, FilterChips, LoadedView, useLoad, type Choice } from '@am/ui';
import { useCallback, useState, type FormEvent, type ReactNode } from 'react';

import type { Analytics, HorizonYield, MarketdataApi, Rate, Spread, WhatIf } from '../api';
import { formatDate, formatNumber, PRICE_LABELS, sourceLabel } from '../format';
import { END_OF_DAY, endOfPreviousMonth, monthGenitive, moscowMoment, shiftDay } from '../moment';
import { REFRESH_MS, useEvery } from '../refresh';

const HORIZON_LABELS = { offer: 'К оферте', maturity: 'К погашению' } as const;

const BASIS_LABELS: Readonly<Record<Analytics['price']['basis'], string>> = {
  valuation: 'цена для оценки',
  price: 'что если: введённая цена',
  yield: 'что если: по введённой доходности',
  spread: 'что если: по введённому спреду',
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

function row(label: string, value: ReactNode) {
  return (
    <div className="kv-row" key={label}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

/**
 * Калькулятор доходности (design/04): доходность и метрики или вменённый спред над
 * фондированием с разложением — на выбранную дату; «что если».
 */
export function AnalyticsBlock({
  api,
  id,
  today,
}: {
  readonly api: MarketdataApi;
  readonly id: string;
  readonly today: string;
}) {
  const [day, setDay] = useState(today);
  const [whatIf, setWhatIf] = useState<WhatIf>({});
  // Сегодня — по текущей цене; другой день — на его конец, как отметки для оценки.
  const at = day === today ? undefined : moscowMoment(day, END_OF_DAY);
  const load = useCallback(() => api.analytics(id, whatIf, at), [api, id, whatIf, at]);
  const [state, reload] = useLoad(load);
  const active = Object.keys(whatIf).length > 0;
  // По текущей цене для оценки расчёт следует за ценой; прошлое и «что если» не обновляем.
  useEvery(reload, REFRESH_MS, !active && at === undefined);
  const type = state.status === 'ready' ? state.data.type : null;
  const quick: readonly Choice<string>[] = [
    { value: today, label: 'Сегодня' },
    { value: shiftDay(today, -1), label: 'Вчера' },
    { value: endOfPreviousMonth(today), label: `Конец ${monthGenitive(endOfPreviousMonth(today))}` },
  ];

  return (
    <section aria-label="Калькулятор доходности" className="price-panel">
      <h3>{type === null ? 'Калькулятор доходности' : TITLES[type]}</h3>
      <div className="analytics-when">
        <input
          type="date"
          aria-label="Дата расчёта"
          value={day}
          max={today}
          onChange={(e) => e.target.value && setDay(e.target.value)}
        />
        <FilterChips label="Быстрый выбор даты" choices={quick} value={day} onChange={setDay} />
      </div>
      <LoadedView state={state}>
        {(found) => (
          <>
            <PriceFacts found={found} past={at !== undefined} />
            {found.spread === null ? (
              <Yields yields={found.yields} />
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

function PriceFacts({ found, past }: { readonly found: Analytics; readonly past: boolean }) {
  const price = found.price;
  const valuation = price.valuation;
  const basis =
    price.basis === 'valuation' && valuation !== null
      ? `${PRICE_LABELS[valuation.type]}, ${sourceLabel(valuation.source)}`
      : BASIS_LABELS[price.basis];
  return (
    <>
      <p className="subline">
        {past ? `На конец ${formatDate(found.trading_day)}` : 'Сейчас'} · расчёты{' '}
        {formatDate(found.settlement_date)}
      </p>
      <dl className="kv" aria-label="Цена в расчёте">
        {row('Чистая цена', `${formatNumber(price.clean, 4)}% · ${basis}`)}
        {row('Номинал', `${formatNumber(price.face_value, 2)} ₽`)}
        {row('НКД', `${formatNumber(price.accrued, 2)} ₽`)}
        {row('Грязная цена', `${formatNumber(price.dirty, 2)} ₽`)}
      </dl>
    </>
  );
}

function Yields({ yields }: { readonly yields: readonly HorizonYield[] }) {
  return (
    <>
      {yields.map((item) => {
        const label = `${HORIZON_LABELS[item.horizon.kind]} ${formatDate(item.horizon.date)}`;
        return (
          <section key={item.horizon.kind} className="horizon" aria-label={label}>
            <div className="horizon-head">
              <span>{label}</span>
              {item.problem === null && <strong>{formatNumber(item.yield_percent, 2)}%</strong>}
            </div>
            {item.problem === null ? (
              <dl className="kv">
                {row(
                  'Дюрация',
                  `${formatNumber(item.macaulay_years, 2)} г · ${formatNumber(item.macaulay_days, 0)} дн.`,
                )}
                {row('Мод. дюрация', formatNumber(item.modified_duration, 2))}
                {row('DV01', `${formatNumber(item.dv01, 4)} ₽`)}
                {row('Выпуклость', formatNumber(item.convexity, 2))}
              </dl>
            ) : (
              <p className="muted">{item.problem.message}</p>
            )}
          </section>
        );
      })}
    </>
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
  const label = `${HORIZON_LABELS[spread.horizon.kind]} ${formatDate(spread.horizon.date)}`;
  return (
    <section className="horizon" aria-label={label}>
      <div className="horizon-head">
        <span>{label}</span>
        <strong>{formatNumber(spread.spread_bp, 1)} бп</strong>
      </div>
      <p className="subline">
        {terms}. Доходность при плоском прогнозе ставки — {formatNumber(spread.yield_percent, 2)}%.
      </p>
      <p className="subline">
        Фондирование: {rateLine(spread.funding)} → {formatNumber(spread.funding_percent, 2)}% эффективных
        годовых.
        {spread.index !== null && spread.index.rate !== 'ruonia' && ` Индекс: ${rateLine(spread.index)}.`}
      </p>
      <dl className="kv" aria-label="Разложение спреда">
        {row('Базис ставки', `${formatNumber(parts.basis_bp, 1)} бп`)}
        {row('Капитализация', `${formatNumber(parts.capitalization_bp, 1)} бп`)}
        {row(type === 'linker' ? 'Купон' : 'Маржа', `${formatNumber(parts.margin_bp, 1)} бп`)}
        {row('Прочее', `${formatNumber(parts.other_bp, 1)} бп`)}
        {row('Поправка на цену', `${formatNumber(parts.price_bp, 1)} бп`)}
        {row('Спред-дюрация', `${formatNumber(spread.spread_duration, 2)} года`)}
        {row('DV01 по спреду', `${formatNumber(spread.spread_dv01, 4)} ₽`)}
      </dl>
    </section>
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
    <form className="what-if" onSubmit={submit} aria-label="Что если">
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
        <input inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} placeholder="99,50" />
      </label>
      <button type="submit">Посчитать</button>
      {active && (
        <button type="button" className="link-button" onClick={() => onApply({})}>
          Вернуть цену для оценки
        </button>
      )}
      <ErrorNote message={error} />
    </form>
  );
}
