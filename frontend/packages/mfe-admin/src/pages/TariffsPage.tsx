import { useCallback, useState, type FormEvent } from 'react';
import { ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';

import type { Account, PortfolioApi, Tariff } from '../api';

/**
 * Тарифы брокера на счёт (решение 151): по ним загрузка из отчёта QUIK считает
 * комиссию. Купля-продажа — % от оборота без НКД, РЕПО — % годовых от суммы первой
 * части. Новый тариф действует с даты начала; загруженные сделки он не меняет.
 */
export function TariffsPage({ api }: { readonly api: PortfolioApi }) {
  const load = useCallback(() => api.accounts(), [api]);
  const [state] = useLoad(load);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  return (
    <LoadedView state={state}>
      {(accounts) => {
        const brokers = accounts.filter((a) => a.cash_kind === 'broker');
        if (brokers.length === 0) {
          return <p className="muted">Брокерских счетов пока нет — заведите их в разделе «Счета».</p>;
        }
        const selected = brokers.find((a) => a.id === selectedId) ?? brokers[0]!;
        return (
          <>
            <p className="muted">
              Комиссию сделок из отчёта QUIK система считает по тарифу счёта на день сделки: купля-продажа — %
              от оборота без НКД, РЕПО — % годовых от суммы первой части за срок РЕПО.
            </p>
            <form className="inline" aria-label="Счёт">
              <label>
                Брокерский счёт
                <select value={selected.id} onChange={(e) => setSelectedId(e.target.value)}>
                  {brokers.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.code} — {a.name}
                    </option>
                  ))}
                </select>
              </label>
            </form>
            <AccountTariffs key={selected.id} account={selected} api={api} />
          </>
        );
      }}
    </LoadedView>
  );
}

function AccountTariffs({ account, api }: { readonly account: Account; readonly api: PortfolioApi }) {
  const load = useCallback(() => api.tariffs(account.id), [api, account.id]);
  const [state, reload] = useLoad(load);

  return (
    <div className="split">
      <LoadedView state={state}>{(tariffs) => <TariffHistory tariffs={tariffs} />}</LoadedView>
      <NewTariffForm account={account} api={api} onDone={reload} />
    </div>
  );
}

function TariffHistory({ tariffs }: { readonly tariffs: readonly Tariff[] }) {
  if (tariffs.length === 0) {
    return <p className="muted">Тарифа у счёта нет: при загрузке ставки придётся задавать вручную.</p>;
  }
  return (
    <table className="data" aria-label="Тарифы счёта">
      <thead>
        <tr>
          <th>С даты</th>
          <th className="num">Купля-продажа, % от оборота</th>
          <th className="num">РЕПО, % годовых</th>
          <th>Кто задал</th>
          <th>Комментарий</th>
        </tr>
      </thead>
      <tbody>
        {[...tariffs].reverse().map((t) => (
          <tr key={`${t.valid_from}-${t.created_at}`}>
            <td>{formatDay(t.valid_from)}</td>
            <td className="num">{percent(t.outright_rate)}</td>
            <td className="num">{percent(t.repo_rate)}</td>
            <td>{t.author}</td>
            <td>{t.comment ?? '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function NewTariffForm({
  account,
  api,
  onDone,
}: {
  readonly account: Account;
  readonly api: PortfolioApi;
  readonly onDone: () => void;
}) {
  const [outright, setOutright] = useState('');
  const [repo, setRepo] = useState('');
  const [validFrom, setValidFrom] = useState('');
  const [comment, setComment] = useState('');
  const { run, error, busy } = useAction(onDone);

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(() =>
      api.setTariff(account.id, {
        outright_rate: decimal(outright),
        repo_rate: decimal(repo),
        valid_from: validFrom,
        comment: comment.trim() || null,
      }),
    );
  }

  return (
    <form onSubmit={submit} aria-label="Новый тариф">
      <h3>Новый тариф счёта {account.code}</h3>
      <div className="fields">
        <label>
          Купля-продажа, % от оборота без НКД
          <input value={outright} required inputMode="decimal" onChange={(e) => setOutright(e.target.value)} />
        </label>
        <label>
          РЕПО, % годовых
          <input value={repo} required inputMode="decimal" onChange={(e) => setRepo(e.target.value)} />
        </label>
        <label>
          Действует с
          <input type="date" value={validFrom} required onChange={(e) => setValidFrom(e.target.value)} />
        </label>
        <label>
          Комментарий
          <input value={comment} onChange={(e) => setComment(e.target.value)} />
        </label>
      </div>
      <button type="submit" disabled={busy}>
        Сохранить тариф
      </button>
      <ErrorNote message={error} />
    </form>
  );
}

/** 0,0125 → «0.0125»: запятая и пробелы из ввода. */
function decimal(value: string): string {
  return value.trim().replace(/\s/g, '').replace(',', '.');
}

/** «0.012500» → «0,0125». */
function percent(value: string): string {
  const trimmed = value.includes('.') ? value.replace(/0+$/, '').replace(/\.$/, '') : value;
  return trimmed.replace('.', ',');
}

function formatDay(day: string): string {
  const [year, month, date] = day.split('-');
  return `${date}.${month}.${year}`;
}
