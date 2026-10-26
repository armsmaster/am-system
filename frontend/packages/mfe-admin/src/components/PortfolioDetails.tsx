import { useState, type FormEvent } from 'react';
import { ErrorNote, useAction } from '@am/ui';

import type { Account, Kind, Portfolio, PortfolioApi } from '../api';
import { kindLabel } from './PortfolioTree';

interface DetailsProps {
  readonly portfolio: Portfolio;
  readonly portfolios: readonly Portfolio[];
  readonly accounts: readonly Account[];
  readonly api: PortfolioApi;
  readonly onChanged: () => void;
}

/** Карточка выбранного портфеля и действия с ним. */
export function PortfolioDetails(props: DetailsProps) {
  const { portfolio, portfolios, accounts } = props;
  const codeOf = (id: string | null) => portfolios.find((p) => p.id === id)?.code ?? '—';
  const accountCode = accounts.find((a) => a.id === portfolio.account_id)?.code ?? '—';

  return (
    <article className="details">
      <h2>
        {portfolio.code} — {portfolio.name}
      </h2>
      <dl className="facts">
        <dt>Тип</dt>
        <dd>{kindLabel(portfolio)}</dd>
        <dt>Фонд</dt>
        <dd>{portfolio.is_fund ? 'это фонд' : codeOf(portfolio.fund_id)}</dd>
        {portfolio.kind === 'physical' && (
          <>
            <dt>Счёт</dt>
            <dd>{accountCode}</dd>
          </>
        )}
      </dl>

      <RenameForm {...props} />
      {portfolio.parent_ids.length > 0 && <ParentsList {...props} />}
      {portfolio.kind === 'compound' && <AddChildForm {...props} />}
      {portfolio.kind === 'compound' && <LinkExistingForm {...props} />}
      {portfolio.kind === 'physical' && <AccountForm {...props} />}
      {portfolio.is_fund && <TreasuryForm {...props} />}
    </article>
  );
}

function RenameForm({ portfolio, api, onChanged }: DetailsProps) {
  const [code, setCode] = useState(portfolio.code);
  const [name, setName] = useState(portfolio.name);
  const { run, error, busy } = useAction(onChanged);

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(() => api.updatePortfolio(portfolio.id, { code, name }));
  }

  return (
    <form onSubmit={submit} aria-label="Код и название">
      <h3>Код и название</h3>
      <label>
        Код
        <input value={code} onChange={(e) => setCode(e.target.value)} required />
      </label>
      <label>
        Название
        <input value={name} onChange={(e) => setName(e.target.value)} required />
      </label>
      <button type="submit" disabled={busy}>
        Сохранить
      </button>
      <ErrorNote message={error} />
    </form>
  );
}

function ParentsList({ portfolio, portfolios, api, onChanged }: DetailsProps) {
  const { run, error, busy } = useAction(onChanged);
  const parents = portfolios.filter((p) => portfolio.parent_ids.includes(p.id));

  return (
    <section aria-label="Входит в">
      <h3>Входит в</h3>
      <ul className="plain">
        {parents.map((parent) => (
          <li key={parent.id}>
            {parent.code}{' '}
            <button
              type="button"
              disabled={busy}
              onClick={() => void run(() => api.unlink(parent.id, portfolio.id))}
            >
              Исключить из {parent.code}
            </button>
          </li>
        ))}
      </ul>
      <ErrorNote message={error} />
    </section>
  );
}

function AddChildForm({ portfolio, accounts, api, onChanged }: DetailsProps) {
  const fundId = portfolio.is_fund ? portfolio.id : portfolio.fund_id;
  const fundAccounts = accounts.filter((a) => a.fund_id === fundId);
  const [kind, setKind] = useState<Kind>('compound');
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [accountId, setAccountId] = useState(fundAccounts[0]?.id ?? '');
  const { run, error, busy } = useAction(onChanged);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const created = await run(() =>
      api.createPortfolio({
        code,
        name,
        kind,
        parent_ids: [portfolio.id],
        account_id: kind === 'physical' ? accountId || null : null,
      }),
    );
    if (created) {
      setCode('');
      setName('');
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} aria-label="Новый портфель внутри">
      <h3>Новый портфель внутри {portfolio.code}</h3>
      <label>
        Тип
        <select value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
          <option value="compound">compound — содержит портфели</option>
          {fundId && <option value="physical">physical — содержит сделки</option>}
        </select>
      </label>
      {!fundId && (
        <p className="muted">Physical создаётся внутри фонда: в представлении у него не будет фонда.</p>
      )}
      <label>
        Код
        <input value={code} onChange={(e) => setCode(e.target.value)} required />
      </label>
      <label>
        Название
        <input value={name} onChange={(e) => setName(e.target.value)} required />
      </label>
      {kind === 'physical' && (
        <label>
          Счёт
          <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
            {fundAccounts.length === 0 && <option value="">У фонда нет счетов</option>}
            {fundAccounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.code} — {a.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <button type="submit" disabled={busy}>
        Создать
      </button>
      <ErrorNote message={error} />
    </form>
  );
}

function LinkExistingForm({ portfolio, portfolios, api, onChanged }: DetailsProps) {
  const candidates = portfolios.filter(
    (p) => p.id !== portfolio.id && !p.parent_ids.includes(portfolio.id),
  );
  const [childId, setChildId] = useState(candidates[0]?.id ?? '');
  const { run, error, busy } = useAction(onChanged);

  if (candidates.length === 0) {
    return null;
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(() => api.link(portfolio.id, childId));
  }

  return (
    <form onSubmit={submit} aria-label="Включить существующий">
      <h3>Включить существующий портфель</h3>
      <label>
        Портфель
        <select value={childId} onChange={(e) => setChildId(e.target.value)}>
          {candidates.map((p) => (
            <option key={p.id} value={p.id}>
              {p.code} — {p.name}
            </option>
          ))}
        </select>
      </label>
      <button type="submit" disabled={busy}>
        Включить
      </button>
      <ErrorNote message={error} />
    </form>
  );
}

function AccountForm({ portfolio, accounts, api, onChanged }: DetailsProps) {
  const fundAccounts = accounts.filter((a) => a.fund_id === portfolio.fund_id);
  const [accountId, setAccountId] = useState(portfolio.account_id ?? '');
  const { run, error, busy } = useAction(onChanged);

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(() => api.setAccount(portfolio.id, accountId));
  }

  return (
    <form onSubmit={submit} aria-label="Пара счетов">
      <h3>Пара счетов</h3>
      <label>
        Счёт
        <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
          {fundAccounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.code} — {a.name}
            </option>
          ))}
        </select>
      </label>
      <button type="submit" disabled={busy}>
        Сменить счёт
      </button>
      <ErrorNote message={error} />
    </form>
  );
}

function TreasuryForm({ portfolio, portfolios, api, onChanged }: DetailsProps) {
  const physicals = portfolios.filter((p) => p.kind === 'physical' && p.fund_id === portfolio.id);
  const [treasuryId, setTreasuryId] = useState(portfolio.treasury_id ?? '');
  const { run, error, busy } = useAction(onChanged);

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(() => api.setTreasury(portfolio.id, treasuryId || null));
  }

  return (
    <form onSubmit={submit} aria-label="Казначейский портфель">
      <h3>Казначейский портфель</h3>
      <p className="muted">Сюда проводятся паи, вознаграждения и расходы фонда.</p>
      <label>
        Портфель
        <select value={treasuryId} onChange={(e) => setTreasuryId(e.target.value)}>
          <option value="">не назначен</option>
          {physicals.map((p) => (
            <option key={p.id} value={p.id}>
              {p.code} — {p.name}
            </option>
          ))}
        </select>
      </label>
      <button type="submit" disabled={busy}>
        Назначить
      </button>
      <ErrorNote message={error} />
    </form>
  );
}
