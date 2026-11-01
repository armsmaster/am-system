import { ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';
import { Fragment, useCallback, useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router';

import type { Context } from '../App';
import type { FeeKind, FeeRate, Nav, NavDay } from '../api';
import {
  decimalInput,
  FEE_LABELS,
  FLAG_LABELS,
  formatDate,
  formatMoney,
  formatPrice,
  formatTime,
  moscowToday,
  sumMoney,
} from '../format';

const FEE_KINDS = Object.keys(FEE_LABELS) as FeeKind[];

/**
 * СЧА фонда по дням и стоимость пая (design/06): наша оценка, не официальная
 * (решение 40). Комиссии от СЧА начисляются каждый календарный день по ставкам
 * фонда; ставки вводит сопровождение.
 */
export function NavPage({ context }: { readonly context: Context }) {
  const funds = context.portfolios.filter((p) => p.is_fund);
  const [params, setParams] = useSearchParams();
  const fundId = params.get('fund') ?? funds[0]?.id ?? '';
  const till = params.get('till') ?? moscowToday();
  const first = params.get('from') ?? `${till.slice(0, 8)}01`;
  const loadNav = useCallback(() => context.api.nav(fundId, first, till), [context.api, fundId, first, till]);
  const loadRates = useCallback(() => context.api.feeRates(fundId), [context.api, fundId]);
  const [nav] = useLoad(loadNav);
  const [rates, reloadRates] = useLoad(loadRates);

  if (funds.length === 0) {
    return <p className="muted">Нет доступных фондов: доступ выдаёт администратор.</p>;
  }
  const update = (changes: Record<string, string>) => setParams({ fund: fundId, from: first, till, ...changes });
  return (
    <>
      <form className="inline" aria-label="Фонд и период" onSubmit={(e) => e.preventDefault()}>
        <label>
          Фонд
          <select value={fundId} onChange={(e) => update({ fund: e.target.value })}>
            {funds.map((p) => (
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
      <LoadedView state={nav}>{(found) => <NavView nav={found} />}</LoadedView>
      <h2>Ставки комиссий от СЧА</h2>
      <LoadedView state={rates}>{(found) => <RatesTable rates={found} />}</LoadedView>
      {context.isOperations && <RateForm context={context} fundId={fundId} onSaved={reloadRates} />}
    </>
  );
}

function NavView({ nav }: { readonly nav: Nav }) {
  const { days } = nav;
  const last = days[days.length - 1];
  const flags = [...new Set(days.flatMap((d) => d.flags))];
  return (
    <>
      {nav.waiting_since && (
        <p className="notice notice-warning">
          Фонд пересчитывается с {formatDate(nav.waiting_since)} — пока показан прежний расчёт.
        </p>
      )}
      {last === undefined ? (
        <p className="muted">Расчёта за период нет.</p>
      ) : (
        <>
          {flags.length > 0 && (
            <p className="notice notice-warning">
              Оценка неполная: {flags.map((f) => FLAG_LABELS[f]).join('; ')}.
            </p>
          )}
          <table className="data" aria-label="СЧА по дням">
            <thead>
              <tr>
                <th>Дата</th>
                <th className="num">СЧА, ₽</th>
                <th className="num">Паёв</th>
                <th className="num">Стоимость пая, ₽</th>
                <th className="num">Комиссии за день, ₽</th>
                <th className="num">Комиссии к выплате, ₽</th>
              </tr>
            </thead>
            <tbody>
              {days.map((d) => (
                <tr key={d.date}>
                  <td>{formatDate(d.date)}</td>
                  <td className="num">{formatMoney(d.nav)}</td>
                  <td className="num">{formatPrice(d.units)}</td>
                  <td className="num">{formatMoney(d.unit_price)}</td>
                  <td className="num">{formatMoney(sumMoney(Object.values(d.fees_today) as string[]))}</td>
                  <td className="num">{formatMoney(d.fees_owed)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Composition day={last} />
        </>
      )}
    </>
  );
}

/** Из чего складывается СЧА на последний день периода. */
function Composition({ day }: { readonly day: NavDay }) {
  const lines = [
    ['деньги на счетах', day.cash, false],
    ['бумаги по чистой цене', day.clean, false],
    ['НКД в оценке и купоны к получению', day.accrued, false],
    ['к получению по сделкам до расчётов', day.receivable, false],
    ['к уплате по сделкам до расчётов', day.payable, true],
    ['размещено в обратное РЕПО, с процентами', day.repo_placed, false],
    ['привлечено в прямое РЕПО, с процентами', day.repo_borrowed, true],
    ['требования к пайщикам', day.claims, false],
    ['обязательства перед пайщиками', day.obligations, true],
    ['начисленные расходы', day.expenses, true],
    ['комиссии от СЧА к выплате', day.fees_owed, true],
  ] as const;
  return (
    <dl className="facts" aria-label={`Состав СЧА на ${formatDate(day.date)}`}>
      {lines
        .filter(([, value]) => Number(value) !== 0)
        .map(([label, value, minus]) => (
          <Fragment key={label}>
            <dt>{minus ? `− ${label}` : label}</dt>
            <dd>{formatMoney(value)} ₽</dd>
          </Fragment>
        ))}
      <dt>СЧА на {formatDate(day.date)}</dt>
      <dd>
        <strong>{formatMoney(day.nav)} ₽</strong>
      </dd>
    </dl>
  );
}

function RatesTable({ rates }: { readonly rates: readonly FeeRate[] }) {
  if (rates.length === 0) {
    return <p className="muted">Ставок нет — комиссии от СЧА не начисляются.</p>;
  }
  return (
    <table className="data" aria-label="Ставки комиссий">
      <thead>
        <tr>
          <th>Комиссия</th>
          <th className="num">Ставка, % годовых</th>
          <th>Действует с</th>
          <th>Комментарий</th>
          <th>Кто ввёл</th>
        </tr>
      </thead>
      <tbody>
        {rates.map((r) => (
          <tr key={`${r.kind}-${r.valid_from}-${r.created_at}`}>
            <td>{FEE_LABELS[r.kind]}</td>
            <td className="num">{formatPrice(r.rate)}</td>
            <td>{formatDate(r.valid_from)}</td>
            <td>{r.comment ?? '—'}</td>
            <td>
              {r.author}, {formatTime(r.created_at)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Новая ставка — новая строка с датой начала; фонд пересчитывается с этой даты. */
function RateForm({
  context,
  fundId,
  onSaved,
}: {
  readonly context: Context;
  readonly fundId: string;
  readonly onSaved: () => void;
}) {
  const [kind, setKind] = useState<FeeKind>('management');
  const [rate, setRate] = useState('');
  const [validFrom, setValidFrom] = useState(moscowToday());
  const [comment, setComment] = useState('');
  const { run, error, busy } = useAction(onSaved);

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(() =>
      context.api.setFeeRate(fundId, {
        kind,
        rate: decimalInput(rate),
        valid_from: validFrom,
        comment: comment.trim() || null,
      }),
    );
  }

  return (
    <form aria-label="Новая ставка" onSubmit={submit}>
      <div className="fields">
        <label>
          Комиссия
          <select value={kind} onChange={(e) => setKind(e.target.value as FeeKind)}>
            {FEE_KINDS.map((k) => (
              <option key={k} value={k}>
                {FEE_LABELS[k]}
              </option>
            ))}
          </select>
        </label>
        <label>
          Ставка, % годовых
          <input inputMode="decimal" value={rate} required onChange={(e) => setRate(e.target.value)} />
        </label>
        <label>
          Действует с
          <input type="date" value={validFrom} required onChange={(e) => setValidFrom(e.target.value)} />
        </label>
        <label>
          Основание
          <input value={comment} onChange={(e) => setComment(e.target.value)} />
        </label>
      </div>
      <button type="submit" disabled={busy}>
        Сохранить ставку
      </button>
      <ErrorNote message={error} />
    </form>
  );
}
