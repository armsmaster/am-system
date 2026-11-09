import { LoadedView, useLoad } from '@am/ui';
import { Fragment, useCallback } from 'react';

import type { Context } from '../App';
import type { DayResult, Instrument, Line, Positions, PortfolioState, RepoLine } from '../api';
import { SelectionForm, useSelection } from '../components/Selection';
import {
  FLAG_LABELS,
  formatDate,
  formatMoney,
  formatPrice,
  formatQuantity,
  formatTime,
  movementLabel,
} from '../format';

/**
 * Позиции на конец дня по двум датам (design/05) и их оценка (design/06):
 * по дате сделки — риск и стоимость, на счёте депо — что придёт или уйдёт
 * к расчётам. Сегодня — по текущей цене. Compound — сумма по своим
 * physical-портфелям.
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
      {view.result && <ResultNote result={view.result} />}
      {view.repos.length > 0 && <RepoTable repos={view.repos} />}
      {view.lines.length === 0 ? (
        <p className="muted">Бумаг нет.</p>
      ) : (
        <>
          <table className="data" aria-label="Позиции">
            <thead>
              <tr>
                <th>Бумага</th>
                <th className="num">По дате сделки</th>
                <th className="num">На счёте депо</th>
                <th className="num">Цена оценки</th>
                <th className="num">Стоимость, ₽</th>
                <th className="num">НКД в оценке, ₽</th>
                <th className="num">Нереализованный, ₽</th>
                <th className="num">Результат дня, ₽</th>
                <th className="num">DV01, ₽</th>
              </tr>
            </thead>
            {view.lines.map((line) => (
              <LineRows key={line.instrument_id} line={line} />
            ))}
          </table>
          {Number(view.dv01_spread) !== 0 && (
            <p className="muted">
              DV01 портфеля за 1 бп: по доходности — {formatMoney(view.dv01_yield)} ₽ (облигации с
              фиксированным купоном), по спреду — {formatMoney(view.dv01_spread)} ₽ (все облигации).
            </p>
          )}
        </>
      )}
      <Movements portfolios={view.portfolios} instruments={instruments} />
    </>
  );
}

/** Открытые сделки РЕПО: долг и начисленные проценты (design/07). */
function RepoTable({ repos }: { readonly repos: readonly RepoLine[] }) {
  return (
    <table className="data" aria-label="РЕПО">
      <thead>
        <tr>
          <th>Портфель</th>
          <th>Сделка</th>
          <th>Обеспечение</th>
          <th className="num">Бумаги, шт.</th>
          <th className="num">Ставка, %</th>
          <th>Части</th>
          <th className="num">Долг, ₽</th>
          <th className="num">Проценты, ₽</th>
          <th className="num">В стоимости, ₽</th>
        </tr>
      </thead>
      <tbody>
        {repos.map((r) => (
          <tr key={r.id}>
            <td>{r.portfolio_code}</td>
            <td>{r.direct ? 'прямое: привлекли' : 'обратное: разместили'}</td>
            <td>
              <span className="code">{r.secid}</span>
            </td>
            <td className="num">{r.quantity ? formatQuantity(r.quantity) : '—'}</td>
            <td className="num">{formatPrice(r.rate)}</td>
            <td>
              {formatDate(r.first_date)} → {formatDate(r.second_date)}: {formatMoney(r.second_amount)} ₽
            </td>
            <td className="num">{formatMoney(r.principal)}</td>
            <td className="num">{formatMoney(r.accrued)}</td>
            <td className="num">{formatMoney(r.value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Стоимость портфеля и результат дня по частям: части в сумме дают результат. */
function ResultNote({ result }: { readonly result: DayResult }) {
  const parts = [
    ['переоценка', result.revaluation],
    ['реализованный результат', result.realized],
    ['купонный доход', result.coupon],
    ['комиссии по сделкам', result.fees],
    ['расходы фонда', result.expenses],
    ['вознаграждение и комиссии от СЧА', result.charges],
    ['корректировка комиссий при выплате', result.correction],
    ['проценты по РЕПО', result.repo],
  ] as const;
  return (
    <>
      <dl className="facts" aria-label="Стоимость и результат дня">
        <dt>стоимость портфеля</dt>
        <dd>{formatMoney(result.value)} ₽</dd>
        <dt>результат дня</dt>
        <dd>
          <strong>{formatMoney(result.total)} ₽</strong>
        </dd>
        {parts.map(([label, value]) => (
          <Fragment key={label}>
            <dt>{label}</dt>
            <dd>{formatMoney(value)} ₽</dd>
          </Fragment>
        ))}
        {Number(result.flows) !== 0 && (
          <>
            <dt>приток: паи и переводы</dt>
            <dd>{formatMoney(result.flows)} ₽</dd>
          </>
        )}
        {Number(result.receivable) !== 0 && (
          <>
            <dt>к получению по сделкам до расчётов</dt>
            <dd>{formatMoney(result.receivable)} ₽</dd>
          </>
        )}
        {Number(result.payable) !== 0 && (
          <>
            <dt>к уплате по сделкам до расчётов</dt>
            <dd>{formatMoney(result.payable)} ₽</dd>
          </>
        )}
      </dl>
      {result.flags.length > 0 && (
        <p className="notice notice-warning">
          Оценка неполная: {result.flags.map((f) => FLAG_LABELS[f]).join('; ')}.
        </p>
      )}
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
          {line.price === null ? '—' : `${formatPrice(line.price)}${unit}`}
          {line.flags.includes('stale_price') && <span className="danger"> *</span>}
        </td>
        <td className="num">{formatMoney(line.clean)}</td>
        <td className="num">{formatMoney(line.accrued_value)}</td>
        <td className="num">{formatMoney(line.unrealized)}</td>
        <td className="num">{formatMoney(line.result)}</td>
        <td className="num">{line.dv01 === null ? '—' : formatMoney(line.dv01)}</td>
      </tr>
      <tr>
        <td colSpan={9}>
          <details>
            <summary>Подробнее и лоты FIFO: {line.lots.length}</summary>
            <dl className="facts" aria-label={`Оценка ${line.secid}`}>
              <dt>средняя цена покупки</dt>
              <dd>{line.average_price === null ? '—' : `${formatPrice(line.average_price)}${unit}`}</dd>
              <dt>стоимость покупки, ₽</dt>
              <dd>{formatMoney(line.cost)}</dd>
              <dt>уплаченный НКД, ₽</dt>
              <dd>{formatMoney(line.accrued_paid)}</dd>
              <dt>результат дня: переоценка, реализованный, купонный доход, комиссии, ₽</dt>
              <dd>
                {[line.revaluation, line.realized, line.coupon, line.fees].map(formatMoney).join(' · ')}
              </dd>
              {Number(line.coupon_due) !== 0 && (
                <>
                  <dt>купоны к получению, ₽</dt>
                  <dd>{formatMoney(line.coupon_due)}</dd>
                </>
              )}
              {Number(line.principal_due) !== 0 && (
                <>
                  <dt>погашение номинала к получению, ₽</dt>
                  <dd>{formatMoney(line.principal_due)}</dd>
                </>
              )}
              {line.duration !== null && (
                <>
                  <dt>{line.metrics_basis === 'spread' ? 'спред-дюрация' : 'модифицированная дюрация'}</dt>
                  <dd>{formatPrice(line.duration)}</dd>
                </>
              )}
              {line.flags.length > 0 && (
                <>
                  <dt>оценка</dt>
                  <dd className="danger">{line.flags.map((f) => FLAG_LABELS[f]).join('; ')}</dd>
                </>
              )}
            </dl>
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
            <td>{movementLabel(m)}</td>
            <td>{secid(m.instrument_id)}</td>
            <td className="num">{m.quantity ? formatQuantity(m.quantity) : '—'}</td>
            <td className="num">{formatMoney(m.cash)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
