import { ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';
import { useCallback, useState } from 'react';

import type { Context } from '../App';
import type { AccountDay, BalanceLine, FundDay, TradeLine } from '../api';
import {
  ACCOUNT_STATUS_LABELS,
  describeDifference,
  formatDate,
  formatMoney,
  formatQuantity,
  lineStatus,
  moscowToday,
  MOVEMENT_LABELS,
  movementTitle,
  previousDay,
  RESOLVED,
  SIDE_LABELS,
} from '../format';

/** Сверка фонда за день по всем его счетам (design/08). */
export function ReconciliationPage({ context }: { readonly context: Context }) {
  const { api, funds } = context;
  const [fundId, setFundId] = useState(funds[0]?.id ?? '');
  const [date, setDate] = useState(() => previousDay(moscowToday()));
  const load = useCallback(() => api.day(fundId, date), [api, fundId, date]);
  const [state, reload] = useLoad(load);

  if (!funds.length) {
    return <p className="muted">Нет фондов, к которым у вас есть доступ.</p>;
  }
  return (
    <>
      <div className="toolbar">
        <label>
          Фонд
          <select value={fundId} onChange={(e) => setFundId(e.target.value)}>
            {funds.map((f) => (
              <option key={f.id} value={f.id}>
                {f.code} — {f.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          День
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
      </div>
      <LoadedView state={state}>{(day) => <FundDayView day={day} context={context} reload={reload} />}</LoadedView>
    </>
  );
}

function FundDayView({
  day,
  context,
  reload,
}: {
  readonly day: FundDay;
  readonly context: Context;
  readonly reload: () => void;
}) {
  const summary = !day.trading
    ? 'Неторговый день: выписок нет, сверять нечего.'
    : day.reconciled
      ? 'Все счета фонда сверены — день можно закрыть.'
      : 'Не все счета сверены: разберите расхождения или введите выписки.';
  return (
    <>
      <p className={day.reconciled ? 'notice' : 'notice notice-error'}>
        {day.code}, {formatDate(day.date)}. {summary}
      </p>
      {day.accounts.map((account) => (
        <AccountView key={account.account_id} account={account} date={day.date} context={context} reload={reload} />
      ))}
    </>
  );
}

function AccountView({
  account,
  date,
  context,
  reload,
}: {
  readonly account: AccountDay;
  readonly date: string;
  readonly context: Context;
  readonly reload: () => void;
}) {
  const { api, isOperations } = context;
  const { run, error, busy } = useAction(reload);
  const [accepting, setAccepting] = useState<string | null>(null);
  const [comment, setComment] = useState('');
  const matched = account.trades.filter((t) => t.status === 'matched' && t.system?.status === 'entered');
  const canAccept = (status: TradeLine['status']) => isOperations && !RESOLVED.has(status);

  const accept = async (line: string) => {
    if (await run(() => api.accept({ account_id: account.account_id, date, line, comment: comment.trim() }))) {
      setAccepting(null);
      setComment('');
    }
  };
  const acceptButton = (line: string, status: TradeLine['status']) =>
    canAccept(status) && (
      <button type="button" onClick={() => setAccepting(line)} disabled={busy}>
        Принять
      </button>
    );
  const kind = account.cash_kind === 'broker' ? 'брокерский' : 'банковский';

  return (
    <section aria-label={account.code}>
      <h2>
        {account.code} — {account.name}
      </h2>
      <p className="muted">
        Счёт {kind}, {account.institution}. Портфели: {account.portfolios.join(', ')}.
      </p>
      <p>Статус: {ACCOUNT_STATUS_LABELS[account.status]}.</p>
      {account.waiting_since && <p className="muted">Фонд ждёт пересчёта с {formatDate(account.waiting_since)}.</p>}
      <ErrorNote message={error} />
      {account.status === 'no_statement' && isOperations && account.previous && (
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            const cash = account.previous!.closing_cash;
            void run(() =>
              api.enter({ account_id: account.account_id, date, opening_cash: cash, closing_cash: cash, movements: [] }),
            );
          }}
        >
          Движений нет: остаток {formatMoney(account.previous.closing_cash)} ₽, как в выписке за{' '}
          {formatDate(account.previous.date)}
        </button>
      )}
      {account.statement && (
        <>
          {account.trades.length > 0 && (
            <table aria-label={`Сделки ${account.code}`}>
              <caption>Сделки дня</caption>
              <thead>
                <tr>
                  <th>Сделка</th>
                  <th>В системе</th>
                  <th>В выписке</th>
                  <th>Статус</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {account.trades.map((t) => (
                  <tr key={t.line}>
                    <td>{tradeTitle(t)}</td>
                    <td>{t.system ? tradeTerms(t.system) : '—'}</td>
                    <td>{t.statement ? tradeTerms(t.statement) : '—'}</td>
                    <td>
                      {lineStatus(t.status, account.cash_kind)}
                      {t.differences.map((d) => (
                        <div key={d.field}>{describeDifference(d)}</div>
                      ))}
                      <Accepted line={t} />
                    </td>
                    <td>{acceptButton(t.line, t.status)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {isOperations && matched.length > 0 && (
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  for (const t of matched) {
                    await api.confirm(t.system!.trade_id, `Сошлась с выпиской за ${formatDate(date)}`);
                  }
                })
              }
            >
              Подтвердить сошедшиеся сделки: {matched.length}
            </button>
          )}
          <table aria-label={`Движения ${account.code}`}>
            <caption>Движения денег</caption>
            <thead>
              <tr>
                <th>Движение</th>
                <th>В системе, ₽</th>
                <th>В выписке, ₽</th>
                <th>Расхождение, ₽</th>
                <th>Статус</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {account.movements.length === 0 && (
                <tr>
                  <td colSpan={6}>Движений нет ни в системе, ни в выписке.</td>
                </tr>
              )}
              {account.movements.map((m) => (
                <tr key={m.line}>
                  <td>
                    {movementTitle(m.kind, m.secid, m.trade)}
                    {m.comments.map((c) => (
                      <div key={c} className="muted">
                        {c}
                      </div>
                    ))}
                  </td>
                  <td>{formatMoney(m.system)}</td>
                  <td>{formatMoney(m.statement)}</td>
                  <td>{formatMoney(m.discrepancy)}</td>
                  <td>
                    {lineStatus(m.status, account.cash_kind)}
                    {m.paired && ` ${formatDate(m.paired)}`}
                    <Accepted line={m} />
                  </td>
                  <td>{acceptButton(m.line, m.status)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <table aria-label={`Остаток ${account.code}`}>
            <caption>Остаток денег</caption>
            <thead>
              <tr>
                <th />
                <th>В системе, ₽</th>
                <th>В выписке, ₽</th>
                <th>Расхождение, ₽</th>
                <th>Статус</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {[account.opening!, account.closing!].map((b) => (
                <tr key={b.line}>
                  <td>{b.line === 'balance:opening' ? 'на начало дня' : 'на конец дня'}</td>
                  <td>{formatMoney(b.system)}</td>
                  <td>{formatMoney(b.statement)}</td>
                  <td>{formatMoney(b.discrepancy)}</td>
                  <td>
                    {balanceStatus(b, account)}
                    <Accepted line={b} />
                  </td>
                  <td>{acceptButton(b.line, b.status)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {account.waiting.length > 0 && (
        <div>
          <p>Принятые расхождения ждут пары:</p>
          <ul>
            {account.waiting.map((w) => (
              <li key={`${w.date}-${w.line}`}>
                с {formatDate(w.date)}: {MOVEMENT_LABELS[w.kind]}
                {w.secid && ` ${w.secid}`} {formatMoney(w.discrepancy)} ₽ — {w.comment}
              </li>
            ))}
          </ul>
        </div>
      )}
      {accepting && (
        <form
          aria-label="Принять расхождение"
          onSubmit={(e) => {
            e.preventDefault();
            void accept(accepting);
          }}
        >
          <label>
            Почему расхождение не нужно исправлять
            <input value={comment} onChange={(e) => setComment(e.target.value)} />
          </label>
          <button type="submit" disabled={busy || comment.trim().length < 3}>
            Принять расхождение
          </button>
          <button type="button" onClick={() => setAccepting(null)}>
            Отмена
          </button>
        </form>
      )}
    </section>
  );
}

function Accepted({ line }: { readonly line: { readonly acceptance: TradeLine['acceptance'] } }) {
  if (!line.acceptance) {
    return null;
  }
  return (
    <div className="muted">
      {line.acceptance.comment} ({line.acceptance.accepted_by})
    </div>
  );
}

function tradeTitle(line: TradeLine): string {
  const trade = line.system ?? line.statement!;
  const number = line.statement?.external_id ?? line.system?.external_id ?? 'без номера';
  const security = line.system?.secid ?? line.statement!.code;
  const what = trade.kind === 'repo' ? `РЕПО, ${SIDE_LABELS[trade.side]}` : SIDE_LABELS[trade.side];
  return `${number}: ${what} ${security}`;
}

function tradeTerms(trade: { readonly quantity: number; readonly amount: string; readonly fee: string }): string {
  return `${formatQuantity(trade.quantity)} шт., ${formatMoney(trade.amount)} ₽, комиссия ${formatMoney(trade.fee)} ₽`;
}

function balanceStatus(line: BalanceLine, account: AccountDay): string {
  const label = lineStatus(line.status, account.cash_kind);
  return line.status === 'explained' ? `${label}: ${formatMoney(line.expected)} ₽` : label;
}
