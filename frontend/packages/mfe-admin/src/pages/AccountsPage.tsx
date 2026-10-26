import { useCallback, useState, type FormEvent } from 'react';
import { ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';

import type { Account, AccountFields, CashKind, Portfolio, PortfolioApi } from '../api';

const CASH_KIND_LABELS: Readonly<Record<CashKind, string>> = {
  broker: 'брокерский',
  bank: 'банковский',
};

const EMPTY: AccountFields = {
  code: '',
  name: '',
  cash_kind: 'broker',
  institution: '',
  cash_account: '',
  depo_account: null,
  stock_market: true,
  derivatives_market: false,
};

export function AccountsPage({ api }: { readonly api: PortfolioApi }) {
  const load = useCallback(() => Promise.all([api.accounts(), api.portfolios()]), [api]);
  const [state, reload] = useLoad(load);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  return (
    <LoadedView state={state}>
      {([accounts, portfolios]) => {
        const funds = portfolios.filter((p) => p.is_fund);
        const selected = accounts.find((a) => a.id === selectedId);
        return (
          <>
            <AccountsTable
              accounts={accounts}
              funds={funds}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />
            <div className="split">
              {selected ? (
                <EditAccountForm key={selected.id} account={selected} api={api} onDone={reload} />
              ) : (
                <p className="muted">Выберите счёт, чтобы изменить его.</p>
              )}
              <NewAccountForm funds={funds} api={api} onDone={reload} />
            </div>
          </>
        );
      }}
    </LoadedView>
  );
}

function AccountsTable({
  accounts,
  funds,
  selectedId,
  onSelect,
}: {
  readonly accounts: readonly Account[];
  readonly funds: readonly Portfolio[];
  readonly selectedId: string | null;
  readonly onSelect: (id: string) => void;
}) {
  if (accounts.length === 0) {
    return <p className="muted">Счетов пока нет.</p>;
  }
  const fundCode = (id: string) => funds.find((f) => f.id === id)?.code ?? '—';
  return (
    <table className="data">
      <thead>
        <tr>
          <th>Код</th>
          <th>Фонд</th>
          <th>Денежный счёт</th>
          <th>Счёт депо</th>
          <th>Рынки</th>
        </tr>
      </thead>
      <tbody>
        {accounts.map((a) => (
          <tr key={a.id} className={a.id === selectedId ? 'selected' : undefined}>
            <td>
              <button type="button" className="link" onClick={() => onSelect(a.id)}>
                {a.code}
              </button>{' '}
              <span className="muted">{a.name}</span>
            </td>
            <td>{fundCode(a.fund_id)}</td>
            <td>
              {CASH_KIND_LABELS[a.cash_kind]}, {a.institution}: {a.cash_account}
            </td>
            <td>{a.depo_account ?? '—'}</td>
            <td>{markets(a)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function markets(account: AccountFields): string {
  const names = [account.stock_market && 'фондовый', account.derivatives_market && 'срочный'];
  return names.filter(Boolean).join(', ') || '—';
}

/** Поля пары счетов — общие для создания и изменения. */
function AccountFieldsEditor({
  value,
  onChange,
}: {
  readonly value: AccountFields;
  readonly onChange: (next: AccountFields) => void;
}) {
  const set = <K extends keyof AccountFields>(key: K, field: AccountFields[K]) =>
    onChange({ ...value, [key]: field });

  return (
    <>
      <label>
        Код
        <input value={value.code} onChange={(e) => set('code', e.target.value)} required />
      </label>
      <label>
        Название
        <input value={value.name} onChange={(e) => set('name', e.target.value)} required />
      </label>
      <label>
        Денежный счёт
        <select
          value={value.cash_kind}
          onChange={(e) => set('cash_kind', e.target.value as CashKind)}
        >
          <option value="broker">брокерский</option>
          <option value="bank">банковский</option>
        </select>
      </label>
      <label>
        Брокер или банк
        <input
          value={value.institution}
          onChange={(e) => set('institution', e.target.value)}
          required
        />
      </label>
      <label>
        Номер денежного счёта
        <input
          value={value.cash_account}
          onChange={(e) => set('cash_account', e.target.value)}
          required
        />
      </label>
      <label>
        Счёт депо (если есть)
        <input
          value={value.depo_account ?? ''}
          onChange={(e) => set('depo_account', e.target.value || null)}
        />
      </label>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={value.stock_market}
          onChange={(e) => set('stock_market', e.target.checked)}
        />
        Фондовый рынок
      </label>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={value.derivatives_market}
          onChange={(e) => set('derivatives_market', e.target.checked)}
        />
        Срочный рынок
      </label>
    </>
  );
}

function NewAccountForm({
  funds,
  api,
  onDone,
}: {
  readonly funds: readonly Portfolio[];
  readonly api: PortfolioApi;
  readonly onDone: () => void;
}) {
  const [fundId, setFundId] = useState(funds[0]?.id ?? '');
  const [fields, setFields] = useState<AccountFields>(EMPTY);
  const { run, error, busy } = useAction(onDone);

  if (funds.length === 0) {
    return <p className="muted">Счёт принадлежит фонду — сначала создайте фонд.</p>;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (await run(() => api.createAccount({ ...fields, fund_id: fundId }))) {
      setFields(EMPTY);
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} aria-label="Новый счёт">
      <h3>Новая пара счетов</h3>
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
      <AccountFieldsEditor value={fields} onChange={setFields} />
      <button type="submit" disabled={busy}>
        Создать
      </button>
      <ErrorNote message={error} />
    </form>
  );
}

function EditAccountForm({
  account,
  api,
  onDone,
}: {
  readonly account: Account;
  readonly api: PortfolioApi;
  readonly onDone: () => void;
}) {
  const { id: _id, fund_id: _fund, ...initial } = account;
  const [fields, setFields] = useState<AccountFields>(initial);
  const { run, error, busy } = useAction(onDone);

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(() => api.updateAccount(account.id, fields));
  }

  return (
    <form onSubmit={submit} aria-label="Изменить счёт">
      <h3>Счёт {account.code}</h3>
      <AccountFieldsEditor value={fields} onChange={setFields} />
      <button type="submit" disabled={busy}>
        Сохранить
      </button>
      <ErrorNote message={error} />
    </form>
  );
}
