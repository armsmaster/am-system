import { LoadedView, useLoad } from '@am/ui';
import { useCallback } from 'react';
import { useSearchParams } from 'react-router';

import type { Context } from '../App';
import type { Account, Ladder, LadderMovement } from '../api';
import { formatDate, formatMoney, formatQuantity, movementLabel } from '../format';

/**
 * Лестница расчётов по счёту (design/05): деньги и бумаги на счёте на конец
 * каждой даты расчётов от сегодня. Дефицит — красным: к расчётам не хватит
 * денег или бумаг, нужно РЕПО или перенос.
 */
export function LadderPage({ context }: { readonly context: Context }) {
  const load = useCallback(() => context.api.accounts(), [context.api]);
  const [state] = useLoad(load);
  return (
    <LoadedView state={state}>
      {(accounts) =>
        accounts.length === 0 ? (
          <p className="muted">Нет доступных счетов: доступ выдаёт администратор.</p>
        ) : (
          <AccountLadder context={context} accounts={accounts} />
        )
      }
    </LoadedView>
  );
}

function AccountLadder({
  context,
  accounts,
}: {
  readonly context: Context;
  readonly accounts: readonly Account[];
}) {
  const [params, setParams] = useSearchParams();
  // Счетов хотя бы один — пустой список страница не показывает.
  const accountId = params.get('account') ?? accounts[0]!.id;
  const load = useCallback(() => context.api.ladder(accountId), [context.api, accountId]);
  const [state] = useLoad(load);
  return (
    <>
      <form className="inline" aria-label="Счёт" onSubmit={(e) => e.preventDefault()}>
        <label>
          Счёт
          <select value={accountId} onChange={(e) => setParams({ account: e.target.value })}>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.code} — {a.name}
              </option>
            ))}
          </select>
        </label>
      </form>
      <LoadedView state={state}>{(ladder) => <LadderView ladder={ladder} />}</LoadedView>
    </>
  );
}

function LadderView({ ladder }: { readonly ladder: Ladder }) {
  const secid = (id: string | null) =>
    id === null ? null : (ladder.instruments.find((i) => i.instrument_id === id)?.secid ?? id);
  // Дефицит называем в день, когда он появился или вырос: дальше он тянется, пока его не перекроют.
  const deficits = ladder.days.flatMap((d, index) => {
    const previous = ladder.days[index - 1];
    const held = (id: string) => previous?.securities.find((s) => s.instrument_id === id)?.quantity ?? 0;
    return [
      ...(Number(d.cash) < Math.min(0, Number(previous?.cash ?? 0))
        ? [`Не хватает ${formatMoney(d.cash.slice(1))} ₽ к расчётам ${formatDate(d.date)}.`]
        : []),
      ...d.securities
        .filter((s) => s.quantity < Math.min(0, held(s.instrument_id)))
        .map(
          (s) =>
            `Не хватает ${formatQuantity(-s.quantity)} ${secid(s.instrument_id)} к расчётам ${formatDate(d.date)}.`,
        ),
    ];
  });
  return (
    <>
      <p className="muted">
        Портфели счёта: {ladder.portfolios.map((p) => p.code).join(', ')}. Прогноз от расчёта на{' '}
        {formatDate(ladder.as_of)}.
      </p>
      {ladder.waiting_since && (
        <p className="notice notice-warning">
          Фонд пересчитывается с {formatDate(ladder.waiting_since)} — лестница по прежнему расчёту.
        </p>
      )}
      {deficits.length > 0 && (
        <div role="alert" className="notice notice-error">
          {deficits.map((text) => (
            <p key={text}>{text} Нужно РЕПО или перенос.</p>
          ))}
        </div>
      )}
      <table className="data" aria-label="Лестница расчётов">
        <thead>
          <tr>
            <th>Дата</th>
            <th>Движения</th>
            <th className="num">Деньги на конец дня, ₽</th>
            {ladder.instruments.map((i) => (
              <th key={i.instrument_id} className="num">
                {i.secid}, шт.
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ladder.days.map((d) => (
            <tr key={d.date}>
              <td>
                {formatDate(d.date)}
                {!d.trading && <span className="muted"> (выходной)</span>}
              </td>
              <td>
                {d.movements.length === 0
                  ? '—'
                  : d.movements.map((m, index) => <MovementLine key={index} movement={m} secid={secid} />)}
              </td>
              <td className={`num${d.cash.startsWith('-') ? ' danger' : ''}`}>{formatMoney(d.cash)}</td>
              {ladder.instruments.map((i) => {
                const quantity = d.securities.find((s) => s.instrument_id === i.instrument_id)?.quantity ?? 0;
                return (
                  <td key={i.instrument_id} className={`num${quantity < 0 ? ' danger' : ''}`}>
                    {formatQuantity(quantity)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function MovementLine({
  movement: m,
  secid,
}: {
  readonly movement: LadderMovement;
  readonly secid: (id: string | null) => string | null;
}) {
  const parts = [
    `${m.portfolio_code}: ${movementLabel(m)}`,
    secid(m.instrument_id),
    m.quantity ? `${m.quantity > 0 ? '+' : ''}${formatQuantity(m.quantity)} шт.` : null,
    m.cash.replace(/[-0.]/g, '') ? `${m.cash.startsWith('-') ? '' : '+'}${formatMoney(m.cash)} ₽` : null,
  ];
  return <div>{parts.filter(Boolean).join(' ')}</div>;
}
