import { errorMessage } from '@am/platform';
import { ErrorNote, FilterChips, LoadedView, useLoad, type Choice } from '@am/ui';
import { useCallback, useState, type FormEvent, type ReactNode } from 'react';

import type { Analytics, Breakdown, HorizonYield, KnownRate, MarketdataApi, Measure, Spread, WhatIf } from '../api';
import { saveFile } from '../download';
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

/** Решение 156: по умолчанию — непрерывная капитализация, приближение однодневной ставки. */
const MEASURES: readonly Choice<Measure>[] = [
  { value: 'continuous', label: 'Непрерывные' },
  { value: 'annual', label: 'Годовые' },
];
const MEASURE_KEY = 'am.marketdata.measure';

type Input = Exclude<keyof WhatIf, 'measure'>;

const INPUT_LABELS: Readonly<Record<Input, Readonly<Record<Measure, string>>>> = {
  price: { continuous: 'Чистая цена, %', annual: 'Чистая цена, %' },
  yield: { continuous: 'Доходность, % непрерывная', annual: 'Доходность, % годовых' },
  spread: { continuous: 'Спред, бп непрерывный', annual: 'Спред, бп годовой' },
};

function storedMeasure(): Measure {
  try {
    return localStorage.getItem(MEASURE_KEY) === 'annual' ? 'annual' : 'continuous';
  } catch {
    return 'continuous';
  }
}

function storeMeasure(measure: Measure): void {
  try {
    localStorage.setItem(MEASURE_KEY, measure);
  } catch {
    // Хранилище недоступно (приватный режим) — выбор просто не запомнится.
  }
}

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
 * фондированием с разложением — на выбранную дату, в непрерывной капитализации или
 * в годовых; «что если»; детализация расчёта в Excel.
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
  const [measure, setMeasure] = useState<Measure>(storedMeasure);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  // Сегодня — по текущей цене; другой день — на его конец, как отметки для оценки.
  const at = day === today ? undefined : moscowMoment(day, END_OF_DAY);
  const load = useCallback(() => api.analytics(id, whatIf, at), [api, id, whatIf, at]);
  const [state, reload] = useLoad(load);
  const active = Object.keys(whatIf).length > 0;
  // По текущей цене для оценки расчёт следует за ценой; прошлое и «что если» не обновляем.
  useEvery(reload, REFRESH_MS, !active && at === undefined);
  const found = state.status === 'ready' ? state.data : null;
  const quick: readonly Choice<string>[] = [
    { value: today, label: 'Сегодня' },
    { value: shiftDay(today, -1), label: 'Вчера' },
    { value: endOfPreviousMonth(today), label: `Конец ${monthGenitive(endOfPreviousMonth(today))}` },
  ];

  function chooseMeasure(next: Measure) {
    setMeasure(next);
    storeMeasure(next);
  }

  async function download(of: Analytics) {
    setSaving(true);
    setSaveError(null);
    try {
      const file = await api.analyticsWorkbook(id, whatIf, at);
      saveFile(file, `Расчёт ${of.instrument.secid} ${formatDate(of.trading_day)}.xlsx`);
    } catch (error) {
      setSaveError(errorMessage(error));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section aria-label="Калькулятор доходности" className="price-panel">
      <div className="panel-head">
        <h3>{found === null ? 'Калькулятор доходности' : TITLES[found.type]}</h3>
        {found !== null && (
          <button
            type="button"
            className="analytics-download"
            title="Детализация расчёта в Excel: формулы, потоки, проверки"
            disabled={saving}
            onClick={() => void download(found)}
          >
            {saving ? 'Готовлю файл…' : 'Скачать расчёт'}
          </button>
        )}
      </div>
      <ErrorNote message={saveError} />
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
      <div className="analytics-measure">
        <span className="muted">Доходность и спред</span>
        <FilterChips label="Капитализация" choices={MEASURES} value={measure} onChange={chooseMeasure} />
      </div>
      <LoadedView state={state}>
        {(data) => (
          <>
            <PriceFacts found={data} past={at !== undefined} />
            {data.spread === null ? (
              <Yields yields={data.yields} measure={measure} />
            ) : (
              <SpreadView spread={data.spread} type={data.type} measure={measure} />
            )}
          </>
        )}
      </LoadedView>
      <WhatIfForm type={found?.type ?? null} measure={measure} active={active} onApply={setWhatIf} />
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

/** Доходность и метрики в выбранном измерении; без непрерывных (старый сервис) — годовые. */
function yieldFigures(item: HorizonYield, measure: Measure) {
  const smooth = item.continuous;
  if (measure === 'annual' || smooth === null) {
    return {
      ...item,
      other: smooth === null ? null : row('Непрерывная', `${formatNumber(smooth.yield_percent, 2)}%`),
    };
  }
  return {
    ...item,
    ...smooth,
    other: row('Эффективная годовая', `${formatNumber(item.yield_percent, 2)}%`),
  };
}

function Yields({ yields, measure }: { readonly yields: readonly HorizonYield[]; readonly measure: Measure }) {
  return (
    <>
      {yields.map((item) => {
        const label = `${HORIZON_LABELS[item.horizon.kind]} ${formatDate(item.horizon.date)}`;
        const shown = yieldFigures(item, measure);
        return (
          <section key={item.horizon.kind} className="horizon" aria-label={label}>
            <div className="horizon-head">
              <span>{label}</span>
              {item.problem === null && <strong>{formatNumber(shown.yield_percent, 2)}%</strong>}
            </div>
            {item.problem === null ? (
              <dl className="kv">
                {shown.other}
                {row(
                  'Дюрация',
                  `${formatNumber(item.macaulay_years, 2)} г · ${formatNumber(item.macaulay_days, 0)} дн.`,
                )}
                {row('Мод. дюрация', formatNumber(shown.modified_duration, 2))}
                {row('DV01', `${formatNumber(shown.dv01, 4)} ₽`)}
                {row('Выпуклость', formatNumber(shown.convexity, 2))}
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

function rateLine(rate: KnownRate): string {
  return `${rate.label} ${formatNumber(rate.value, 2)}% на ${formatDate(rate.date)}, ${sourceLabel(rate.source)}`;
}

function SpreadView({
  spread,
  type,
  measure,
}: {
  readonly spread: Spread;
  readonly type: Analytics['type'];
  readonly measure: Measure;
}) {
  const continuous = measure === 'continuous';
  const shown = continuous ? spread.continuous : spread;
  const other = continuous ? spread : spread.continuous;
  const parts: Breakdown = shown.breakdown;
  const funding = spread.funding;
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
        <strong>{formatNumber(shown.spread_bp, 1)} бп</strong>
      </div>
      <p className="subline">
        {terms}. Доходность при плоском прогнозе ставки — {formatNumber(shown.yield_percent, 2)}%.
      </p>
      <p className="subline">
        Фондирование: RUONIA {formatNumber(funding.value_percent, 2)}% = КС {formatNumber(funding.key_rate.value, 2)}% +
        базис {formatNumber(funding.basis_bp, 1)} бп → {formatNumber(shown.funding_percent, 2)}%{' '}
        {continuous ? 'непрерывных' : 'эффективных годовых'}.
        {spread.index !== null && ` Индекс: ${rateLine(spread.index)}.`}
      </p>
      <p className="subline muted">
        Базис — DEMA разницы RUONIA и КС с полураспадом полгода, по {formatDate(funding.basis_through)}. Последняя{' '}
        {rateLine(funding.ruonia)}.
      </p>
      <dl className="kv" aria-label="Разложение спреда">
        {row('Базис ставки', `${formatNumber(parts.basis_bp, 1)} бп`)}
        {row('Капитализация', `${formatNumber(parts.capitalization_bp, 1)} бп`)}
        {row(type === 'linker' ? 'Купон' : 'Маржа', `${formatNumber(parts.margin_bp, 1)} бп`)}
        {row('Прочее', `${formatNumber(parts.other_bp, 1)} бп`)}
        {row('Поправка на цену', `${formatNumber(parts.price_bp, 1)} бп`)}
        {row('Спред-дюрация', `${formatNumber(shown.spread_duration, 2)} года`)}
        {row('DV01 по спреду', `${formatNumber(shown.spread_dv01, 4)} ₽`)}
        {row(continuous ? 'Спред в годовых' : 'Спред непрерывный', `${formatNumber(other.spread_bp, 1)} бп`)}
      </dl>
    </section>
  );
}

function WhatIfForm({
  type,
  measure,
  active,
  onApply,
}: {
  /** null — расчёт не удался, тип бумаги неизвестен: предлагаем всё. */
  readonly type: Analytics['type'] | null;
  readonly measure: Measure;
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
    // Доходность и спред — в том измерении, что выбрано сейчас; цене измерение не нужно.
    onApply(input === 'price' ? { price: number } : { [input]: number, measure });
  }

  return (
    <form className="what-if" onSubmit={submit} aria-label="Что если">
      <label>
        Что если
        <select value={input} onChange={(e) => setInput(e.target.value as Input)}>
          {inputs.map((name) => (
            <option key={name} value={name}>
              {INPUT_LABELS[name][measure]}
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
