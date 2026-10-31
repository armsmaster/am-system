import { LoadedView, useLoad } from '@am/ui';
import { useCallback, useState } from 'react';
import { Link } from 'react-router';

import type { Context } from '../App';
import type { Trade } from '../api';
import { SelectionForm, useSelection } from '../components/Selection';
import { TradeForm } from '../components/TradeForm';
import {
  formatDate,
  formatMoney,
  formatPrice,
  formatQuantity,
  formatTime,
  SIDE_LABELS,
  STATUS_LABELS,
} from '../format';

/** Сделки портфеля за день: список и ввод новой сделки. */
export function TradesPage({ context }: { readonly context: Context }) {
  const { portfolioId, date, update } = useSelection(context.portfolios);
  const load = useCallback(() => context.api.trades(portfolioId, date), [context.api, portfolioId, date]);
  const [state, reload] = useLoad(load);
  const [adding, setAdding] = useState(false);
  const [saved, setSaved] = useState<Trade | null>(null);

  if (context.portfolios.length === 0) {
    return <p className="muted">Нет доступных портфелей: доступ выдаёт администратор.</p>;
  }
  const selected = context.portfolios.find((p) => p.id === portfolioId);

  return (
    <>
      <SelectionForm
        portfolios={context.portfolios}
        portfolioId={portfolioId}
        date={date}
        onChange={update}
      />
      {adding ? (
        <TradeForm
          context={context}
          defaultPortfolio={selected?.kind === 'physical' ? selected.id : undefined}
          onSaved={(trade) => {
            setSaved(trade);
            setAdding(false);
            reload();
          }}
          onCancel={() => setAdding(false)}
        />
      ) : (
        <p>
          <button type="button" onClick={() => setAdding(true)}>
            Новая сделка
          </button>
        </p>
      )}
      {saved && <SavedNote trade={saved} basePath={context.basePath} />}
      <LoadedView state={state}>
        {(trades) => <TradesTable trades={trades} basePath={context.basePath} />}
      </LoadedView>
    </>
  );
}

function SavedNote({ trade, basePath }: { readonly trade: Trade; readonly basePath: string }) {
  return (
    <div role="status">
      <p className="notice">
        Сделка введена: <Link to={`${basePath}/trades/${trade.id}`}>{trade.secid}</Link>,{' '}
        {SIDE_LABELS[trade.side]} {formatQuantity(trade.quantity)} шт., сумма{' '}
        {formatMoney(trade.amount)} ₽, расчёты {formatDate(trade.settle_date)}.
      </p>
      {trade.warnings.map((warning) => (
        <p key={warning} className="notice notice-warning">
          {warning}
        </p>
      ))}
    </div>
  );
}

function TradesTable({ trades, basePath }: { readonly trades: readonly Trade[]; readonly basePath: string }) {
  if (trades.length === 0) {
    return <p className="muted">Сделок за этот день нет.</p>;
  }
  return (
    <table className="data" aria-label="Сделки за день">
      <thead>
        <tr>
          <th>Время, МСК</th>
          <th>Портфель</th>
          <th>Бумага</th>
          <th>Направление</th>
          <th className="num">Количество</th>
          <th className="num">Цена</th>
          <th className="num">НКД</th>
          <th className="num">Сумма, ₽</th>
          <th>Расчёты</th>
          <th>Статус</th>
        </tr>
      </thead>
      <tbody>
        {trades.map((t) => (
          <tr key={t.id} className={t.status === 'cancelled' ? 'muted' : undefined}>
            <td>{formatTime(t.trade_at)}</td>
            <td>{t.portfolio_code}</td>
            <td>
              <Link to={`${basePath}/trades/${t.id}`}>{t.secid}</Link>
            </td>
            <td>{SIDE_LABELS[t.side]}</td>
            <td className="num">{formatQuantity(t.quantity)}</td>
            <td className="num">{formatPrice(t.price)}</td>
            <td className="num">{formatPrice(t.accrued)}</td>
            <td className="num">{formatMoney(t.amount)}</td>
            <td>{formatDate(t.settle_date)}</td>
            <td>{STATUS_LABELS[t.status]}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
