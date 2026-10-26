import { ROLE_LABELS } from '@am/platform';
import { useCallback, useState, type FormEvent } from 'react';
import { ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';

import type { Portfolio, PortfolioApi, User } from '../api';

/**
 * Доступы: кто какие портфели видит. Доступ выдаётся на compound
 * и распространяется на всех его потомков. Администратор видит всё.
 */
export function AccessPage({ api }: { readonly api: PortfolioApi }) {
  const load = useCallback(() => Promise.all([api.users(), api.portfolios()]), [api]);
  const [state, reload] = useLoad(load);

  return (
    <LoadedView state={state}>
      {([users, portfolios]) => {
        const compounds = portfolios.filter((p) => p.kind === 'compound');
        return (
          <table className="data">
            <thead>
              <tr>
                <th>Пользователь</th>
                <th>Роли</th>
                <th>Доступ к портфелям</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <UserRow
                  key={user.subject}
                  user={user}
                  compounds={compounds}
                  api={api}
                  onDone={reload}
                />
              ))}
            </tbody>
          </table>
        );
      }}
    </LoadedView>
  );
}

function UserRow({
  user,
  compounds,
  api,
  onDone,
}: {
  readonly user: User;
  readonly compounds: readonly Portfolio[];
  readonly api: PortfolioApi;
  readonly onDone: () => void;
}) {
  const available = compounds.filter((p) => !user.grant_ids.includes(p.id));
  const [portfolioId, setPortfolioId] = useState(available[0]?.id ?? '');
  const { run, error, busy } = useAction(onDone);
  const codeOf = (id: string) => compounds.find((p) => p.id === id)?.code ?? id;
  const isAdmin = user.roles.includes('admin');

  function grant(event: FormEvent) {
    event.preventDefault();
    void run(() => api.grant(user.subject, portfolioId));
  }

  return (
    <tr>
      <td>
        {user.username}
        <div className="muted">{user.full_name}</div>
      </td>
      <td>{user.roles.map((role) => ROLE_LABELS[role]).join(', ') || '—'}</td>
      <td>
        {isAdmin ? (
          <span className="muted">видит всё</span>
        ) : (
          <>
            <ul className="chips" aria-label={`Доступы ${user.username}`}>
              {user.grant_ids.map((id) => (
                <li key={id}>
                  {codeOf(id)}
                  <button
                    type="button"
                    aria-label={`Отозвать ${codeOf(id)} у ${user.username}`}
                    disabled={busy}
                    onClick={() => void run(() => api.revoke(user.subject, id))}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
            {available.length > 0 && (
              <form onSubmit={grant} aria-label={`Выдать доступ ${user.username}`} className="inline">
                <select
                  aria-label="Портфель"
                  value={portfolioId}
                  onChange={(e) => setPortfolioId(e.target.value)}
                >
                  {available.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.code} — {p.name}
                    </option>
                  ))}
                </select>
                <button type="submit" disabled={busy}>
                  Выдать
                </button>
              </form>
            )}
          </>
        )}
        <ErrorNote message={error} />
      </td>
    </tr>
  );
}
