import { Drawer, FilterChips, LoadedView, useLoad, type Choice } from '@am/ui';
import { useCallback, useState, type KeyboardEvent } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router';

import type { Context } from '../App';
import type { Portfolio, Trade } from '../api';
import { ImportPanel } from '../components/ImportPanel';
import { TradeDetails } from '../components/TradeDetails';
import { TradeForm } from '../components/TradeForm';
import {
  clockTime,
  dayTitle,
  formatDate,
  formatMoney,
  formatPrice,
  formatQuantity,
  INSTRUMENT_KIND_LABELS,
  moscowToday,
  shiftDay,
  shortMoney,
  STATUS_LABELS,
  sumMoney,
  tradeLabel,
  tradesCount,
} from '../format';

type Period = 'today' | 'yesterday' | 'week' | 'month' | 'custom';
type Panel = 'new' | 'import' | null;

/** Быстрые периоды (решение 161): неделя и месяц — последние 7 и 30 дней со сегодня. */
function ranges(today: string): Record<Exclude<Period, 'custom'>, readonly [string, string]> {
  return {
    today: [today, today],
    yesterday: [shiftDay(today, -1), shiftDay(today, -1)],
    week: [shiftDay(today, -6), today],
    month: [shiftDay(today, -29), today],
  };
}

const PERIOD_LABELS: Readonly<Record<Period, string>> = {
  today: 'Сегодня',
  yesterday: 'Вчера',
  week: 'Неделя',
  month: 'Месяц',
  custom: 'Период',
};

/**
 * Портфель и период — в адресе страницы: их видно в ссылке, и они переживают
 * открытие сделки. По умолчанию — первый фонд и сегодня; старый ``date`` — один день.
 */
function useFilter(portfolios: readonly Portfolio[]) {
  const [params, setParams] = useSearchParams();
  const today = moscowToday();
  const fallback = portfolios.find((p) => p.is_fund) ?? portfolios[0];
  const portfolioId = params.get('portfolio') ?? fallback?.id ?? '';
  const from = params.get('from') ?? params.get('date') ?? today;
  const till = params.get('till') ?? params.get('date') ?? from;
  const update = (changes: { portfolio?: string; from?: string; till?: string }) =>
    setParams({
      portfolio: changes.portfolio ?? portfolioId,
      from: changes.from ?? from,
      till: changes.till ?? till,
    });
  return { today, portfolioId, from, till, update };
}

/**
 * Сделки портфеля за период (решение 161): по дням, итоги сверху. Строка открывает
 * сделку в панели справа; ввод новой сделки и импорт — тоже в панели.
 */
export function TradesPage({
  context,
  tradeId,
  startPanel = null,
}: {
  readonly context: Context;
  /** Открытая сделка — из адреса …/trades/:id. */
  readonly tradeId?: string | undefined;
  readonly startPanel?: Panel;
}) {
  const { today, portfolioId, from, till, update } = useFilter(context.portfolios);
  const navigate = useNavigate();
  const { search } = useLocation();
  const load = useCallback(() => context.api.trades(portfolioId, from, till), [context.api, portfolioId, from, till]);
  const [state, reload] = useLoad(load);
  const [panel, setPanel] = useState<Panel>(startPanel);
  const [picking, setPicking] = useState(false);
  const [saved, setSaved] = useState<Trade | null>(null);

  if (context.portfolios.length === 0) {
    return <p className="muted">Нет доступных портфелей: доступ выдаёт администратор.</p>;
  }
  const selected = context.portfolios.find((p) => p.id === portfolioId);
  const quick = ranges(today);
  const matched = (Object.keys(quick) as Exclude<Period, 'custom'>[]).find(
    (key) => quick[key][0] === from && quick[key][1] === till,
  );
  const period: Period = picking || matched === undefined ? 'custom' : matched;
  const choices: Choice<Period>[] = (Object.keys(PERIOD_LABELS) as Period[]).map((value) => ({
    value,
    label: PERIOD_LABELS[value],
  }));

  function choose(next: Period) {
    setPicking(next === 'custom');
    if (next !== 'custom') {
      const [since, last] = quick[next];
      update({ from: since, till: last });
    }
  }

  const list = `${context.basePath}${search}`;
  const closePanel = () => {
    setPanel(null);
    if (startPanel !== null) {
      void navigate(list);
    }
  };

  return (
    <>
      <div className="toolbar trades-toolbar">
        <select aria-label="Портфель" value={portfolioId} onChange={(e) => update({ portfolio: e.target.value })}>
          {context.portfolios.map((p) => (
            <option key={p.id} value={p.id}>
              {p.code} — {p.name}
            </option>
          ))}
        </select>
        <FilterChips label="Период" choices={choices} value={period} onChange={choose} />
        {period === 'custom' && (
          <span className="period-dates">
            <input
              type="date"
              aria-label="С даты"
              value={from}
              max={till}
              onChange={(e) => e.target.value && update({ from: e.target.value })}
            />
            —
            <input
              type="date"
              aria-label="По дату"
              value={till}
              min={from}
              onChange={(e) => e.target.value && update({ till: e.target.value })}
            />
          </span>
        )}
        <span className="toolbar-end">
          <button type="button" onClick={() => setPanel('import')}>
            Импорт
          </button>
          <button type="button" className="primary" onClick={() => setPanel('new')}>
            Новая сделка
          </button>
        </span>
      </div>
      {saved && <SavedNote trade={saved} basePath={context.basePath} search={search} />}
      <LoadedView state={state}>
        {(trades) => (
          <>
            <Totals trades={trades} />
            <TradesTable
              trades={trades}
              days={from !== till}
              empty={from === today && till === today ? 'Сделок за сегодня нет.' : 'Сделок за этот период нет.'}
              onOpen={(trade) => void navigate(`${context.basePath}/trades/${trade.id}${search}`)}
              linkTo={(trade) => `${context.basePath}/trades/${trade.id}${search}`}
            />
          </>
        )}
      </LoadedView>
      <Drawer title="Новая сделка" open={panel === 'new'} onClose={closePanel}>
        <TradeForm
          context={context}
          defaultPortfolio={selected?.kind === 'physical' ? selected.id : undefined}
          onSaved={(trade) => {
            setSaved(trade);
            closePanel();
            reload();
          }}
          onCancel={closePanel}
        />
      </Drawer>
      <Drawer title="Импорт сделок" open={panel === 'import'} onClose={closePanel} wide>
        <ImportPanel context={context} onImported={reload} />
      </Drawer>
      {tradeId !== undefined && (
        <Drawer title="Сделка" open onClose={() => void navigate(list)}>
          <TradeDetails key={tradeId} id={tradeId} context={context} onChanged={reload} />
        </Drawer>
      )}
    </>
  );
}

function SavedNote({
  trade,
  basePath,
  search,
}: {
  readonly trade: Trade;
  readonly basePath: string;
  readonly search: string;
}) {
  return (
    <div role="status">
      <p className="notice">
        Сделка введена: <Link to={`${basePath}/trades/${trade.id}${search}`}>{trade.secid}</Link>, {tradeLabel(trade)}
        {trade.quantity ? ` ${formatQuantity(trade.quantity)} шт.` : ''}, сумма {formatMoney(trade.amount)} ₽, расчёты{' '}
        {formatDate(trade.settle_date)}
        {trade.second_date && `, вторая часть ${formatDate(trade.second_date)}`}.
      </p>
      {trade.warnings.map((warning) => (
        <p key={warning} className="notice notice-warning">
          {warning}
        </p>
      ))}
    </div>
  );
}

/** Итоги периода без отменённых: сколько сделок, куплено, продано, комиссии. */
function Totals({ trades }: { readonly trades: readonly Trade[] }) {
  const live = trades.filter((t) => t.status !== 'cancelled');
  const outright = (side: Trade['side']) =>
    sumMoney(live.filter((t) => t.kind === 'outright' && t.side === side).map((t) => t.amount));
  return (
    <dl className="tiles trade-totals" aria-label="Итоги периода">
      <div className="tile">
        <dt>Сделок</dt>
        <dd>{live.length}</dd>
      </div>
      <div className="tile">
        <dt>Куплено</dt>
        <dd>{shortMoney(outright('buy'))}</dd>
      </div>
      <div className="tile">
        <dt>Продано</dt>
        <dd>{shortMoney(outright('sell'))}</dd>
      </div>
      <div className="tile">
        <dt>Комиссии</dt>
        <dd>{shortMoney(sumMoney(live.map((t) => t.fee)))}</dd>
      </div>
    </dl>
  );
}

function TradesTable({
  trades,
  days,
  empty,
  onOpen,
  linkTo,
}: {
  readonly trades: readonly Trade[];
  /** Период больше дня — строки по дням с заголовком дня. */
  readonly days: boolean;
  readonly empty: string;
  readonly onOpen: (trade: Trade) => void;
  readonly linkTo: (trade: Trade) => string;
}) {
  if (trades.length === 0) {
    return <p className="muted">{empty}</p>;
  }
  const latest = [...trades].sort((a, b) => b.trade_at.localeCompare(a.trade_at));
  const dates = [...new Set(latest.map((t) => t.trade_date))];
  return (
    <table className="data trades-table" aria-label="Сделки">
      <thead>
        <tr>
          <th>Время</th>
          <th>Бумага</th>
          <th>Сделка</th>
          <th className="num">Кол-во · цена</th>
          <th className="num">Сумма, ₽</th>
          <th className="hide-sm">Расчёты</th>
          <th className="hide-sm">Статус</th>
        </tr>
      </thead>
      <tbody>
        {dates.map((date) => {
          const ofDay = latest.filter((t) => t.trade_date === date);
          return [
            days && (
              <tr key={date} className="day-row">
                <td colSpan={7}>
                  {dayTitle(date)} · {tradesCount(ofDay.length)}
                </td>
              </tr>
            ),
            ...ofDay.map((t) => <TradeRow key={t.id} trade={t} onOpen={onOpen} link={linkTo(t)} />),
          ];
        })}
      </tbody>
    </table>
  );
}

function TradeRow({
  trade: t,
  onOpen,
  link,
}: {
  readonly trade: Trade;
  readonly onOpen: (trade: Trade) => void;
  readonly link: string;
}) {
  const open = () => onOpen(t);
  const key = (event: KeyboardEvent) => {
    if (event.key === 'Enter') {
      open();
    }
  };
  const details = [
    INSTRUMENT_KIND_LABELS[t.instrument_kind],
    t.portfolio_code,
    t.venue === 'negotiated' ? 'РПС' : null,
    t.counterparty,
  ].filter(Boolean);
  return (
    <tr className={`link-row${t.status === 'cancelled' ? ' cancelled' : ''}`} tabIndex={0} onClick={open} onKeyDown={key}>
      <td className="t-time">{clockTime(t.trade_at)}</td>
      <td className="t-paper">
        <div className="cell-head">
          <Link to={link} className="tag" onClick={(e) => e.stopPropagation()}>
            {t.short_name}
          </Link>
          {t.instrument_kind !== 'ksu' && <span className="mono">{t.secid}</span>}
        </div>
        <div className="subline">{details.join(' · ')}</div>
      </td>
      {/* На телефоне колонки «Время» нет: время — после вида сделки (data-time в стилях). */}
      <td className="t-deal" data-time={clockTime(t.trade_at)}>
        <span className={`pill side-${t.kind === 'repo' ? 'repo' : t.side}`}>{tradeLabel(t)}</span>
        {t.kind === 'repo' && (
          <div className="subline">
            {formatPrice(t.repo_rate)}% · до {formatDate(t.second_date)}
          </div>
        )}
      </td>
      <td className="num t-qty">
        {t.quantity ? (
          <>
            <div className="qty">{formatQuantity(t.quantity)} шт.</div>
            <div className="unit-price">
              {formatPrice(t.price)}
              {t.instrument_kind === 'bond' ? '%' : ' ₽'}
            </div>
          </>
        ) : (
          '—'
        )}
      </td>
      <td className="num t-sum">
        {formatMoney(t.amount)}
        {Number(t.fee) > 0 && <div className="subline">комиссия {formatMoney(t.fee)}</div>}
      </td>
      <td className="hide-sm">
        {formatDate(t.settle_date)}
        {t.second_date && <div className="subline">→ {formatDate(t.second_date)}</div>}
      </td>
      <td className="hide-sm">
        <span className={`pill status-${t.status}`}>{STATUS_LABELS[t.status]}</span>
      </td>
    </tr>
  );
}
