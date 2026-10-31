import { LoadedView, useLoad } from '@am/ui';
import { Fragment, useCallback } from 'react';

import type { Context } from '../App';
import type { Instrument, Line, Positions, PortfolioState } from '../api';
import { SelectionForm, useSelection } from '../components/Selection';
import {
  formatDate,
  formatMoney,
  formatPrice,
  formatQuantity,
  formatTime,
  MOVEMENT_LABELS,
} from '../format';

/**
 * Позиции на конец дня по двум датам (design/05): по дате сделки — риск
 * и стоимость, на счёте депо — что придёт или уйдёт к расчётам. Compound —
 * сумма по своим physical-портфелям.
 */
export function PositionsPage({ context }: { readonly context: Context }) {
  const { portfolioId, date, update } = useSelection(context.portfolios);
  const load = useCallback(
    () => context.api.positions(portfolioId, date),
    [context.api, portfolioId, date],
  );
  const [state] = useLoad(load);

  if (context.portfolios.length === 0) {
    return <p className="muted">Нет доступных портфелей: доступ выдаёт администратор.</p>;
  }
  return (
    <>
      <SelectionForm
        portfolios={context.portfolios}
        portfolioId={portfolioId}
        date={date}
        onChange={update}
      />
      <LoadedView state={state}>
        {(view) => <PositionsView view={view} instruments={context.instruments} />}
      </LoadedView>
    </>
  );
}

function PositionsView({
  view,
  instruments,
}: {
  readonly view: Positions;
  readonly instruments: readonly Instrument[];
}) {
  return (
    <>
      {view.portfolios.map((p) => (
        <StatusNote key={p.id} state={p} date={view.date} />
      ))}
      <p>
        Деньги по дате расчётов на конец {formatDate(view.date)}:{' '}
        <strong>{formatMoney(view.cash)} ₽</strong>
      </p>
      <FundFiguresNote view={view} />
      {view.lines.length === 0 ? (
        <p className="muted">Бумаг нет.</p>
      ) : (
        <table className="data" aria-label="Позиции">
          <thead>
            <tr>
              <th>Бумага</th>
              <th className="num">По дате сделки</th>
              <th className="num">На счёте депо</th>
              <th className="num">Средняя цена</th>
              <th className="num">Стоимость, ₽</th>
              <th className="num">Уплаченный НКД, ₽</th>
            </tr>
          </thead>
          {view.lines.map((line) => (
            <LineRows key={line.instrument_id} line={line} />
          ))}
        </table>
      )}
      <Movements portfolios={view.portfolios} instruments={instruments} />
    </>
  );
}

/** Паи, требования, обязательства и расходы — если у портфеля они есть. */
function FundFiguresNote({ view }: { readonly view: Positions }) {
  const figures = [
    ['паи фонда', view.units, ' шт.'],
    ['требования к пайщикам', view.claims, ' ₽'],
    ['обязательства перед пайщиками', view.obligations, ' ₽'],
    ['начисленные расходы', view.expenses, ' ₽'],
  ] as const;
  const shown = figures.filter(([, value]) => Number(value) !== 0);
  if (shown.length === 0) {
    return null;
  }
  return (
    <dl className="facts" aria-label="Паи и расчёты с пайщиками">
      {shown.map(([label, value, unit]) => (
        <Fragment key={label}>
          <dt>{label}</dt>
          <dd>
            {unit === ' шт.' ? formatPrice(value) : formatMoney(value)}
            {unit}
          </dd>
        </Fragment>
      ))}
    </dl>
  );
}

function StatusNote({ state, date }: { readonly state: PortfolioState; readonly date: string }) {
  if (state.waiting_since !== null) {
    return (
      <p className="notice notice-warning">
        {state.code}: пересчитывается с {formatDate(state.waiting_since)} — пока показан прежний
        расчёт.
      </p>
    );
  }
  if (state.as_of === null) {
    return <p className="muted">{state.code}: расчёта ещё нет — сделок до этой даты не было.</p>;
  }
  if (state.as_of !== date) {
    return (
      <p className="muted">
        {state.code}: последний расчёт — на {formatDate(state.as_of)}.
      </p>
    );
  }
  return null;
}

function LineRows({ line }: { readonly line: Line }) {
  const unit = line.kind === 'bond' ? '%' : ' ₽';
  return (
    <tbody>
      <tr>
        <td>
          <span className="code">{line.secid}</span> — {line.short_name}
        </td>
        <td className="num">{formatQuantity(line.trade_quantity)}</td>
        <td className="num">{formatQuantity(line.settle_quantity)}</td>
        <td className="num">
          {line.average_price === null ? '—' : `${formatPrice(line.average_price)}${unit}`}
        </td>
        <td className="num">{formatMoney(line.cost)}</td>
        <td className="num">{formatMoney(line.accrued_paid)}</td>
      </tr>
      <tr>
        <td colSpan={6}>
          <details>
            <summary>Лоты FIFO: {line.lots.length}</summary>
            <table className="data" aria-label={`Лоты ${line.secid}`}>
              <thead>
                <tr>
                  <th>Портфель</th>
                  <th>Открыт</th>
                  <th className="num">Количество</th>
                  <th className="num">Цена</th>
                  <th className="num">Стоимость, ₽</th>
                </tr>
              </thead>
              <tbody>
                {line.lots.map((lot) => (
                  <tr key={`${lot.portfolio_code}-${lot.trade_id}`}>
                    <td>{lot.portfolio_code}</td>
                    <td>{formatTime(lot.opened_at)}</td>
                    <td className="num">{formatQuantity(lot.quantity)}</td>
                    <td className="num">
                      {formatPrice(lot.price)}
                      {unit}
                    </td>
                    <td className="num">{formatMoney(lot.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </td>
      </tr>
    </tbody>
  );
}

function Movements({
  portfolios,
  instruments,
}: {
  readonly portfolios: readonly PortfolioState[];
  readonly instruments: readonly Instrument[];
}) {
  const rows = portfolios.flatMap((p) => p.movements.map((m, index) => ({ p, m, index })));
  if (rows.length === 0) {
    return null;
  }
  const secid = (id: string | null) =>
    id === null ? '—' : (instruments.find((i) => i.id === id)?.secid ?? id);
  return (
    <table className="data" aria-label="Движения дня">
      <thead>
        <tr>
          <th>Портфель</th>
          <th>Движение</th>
          <th>Бумага</th>
          <th className="num">Бумаги, шт.</th>
          <th className="num">Деньги, ₽</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(({ p, m, index }) => (
          <tr key={`${p.id}-${index}`}>
            <td>{p.code}</td>
            <td>{MOVEMENT_LABELS[m.kind]}</td>
            <td>{secid(m.instrument_id)}</td>
            <td className="num">{m.quantity ? formatQuantity(m.quantity) : '—'}</td>
            <td className="num">{formatMoney(m.cash)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
