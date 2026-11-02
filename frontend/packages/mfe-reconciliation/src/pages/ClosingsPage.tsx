import { LoadedView, useLoad } from '@am/ui';
import { useCallback, useState } from 'react';

import type { Context } from '../App';
import type { Closing } from '../api';
import { formatDate, formatTime } from '../format';

/** Закрытые дни фонда: до какого дня закрыто, кто и когда закрывал и открывал (design/08). */
export function ClosingsPage({ context }: { readonly context: Context }) {
  const { api, funds } = context;
  const [fundId, setFundId] = useState(funds[0]?.id ?? '');
  const load = useCallback(() => api.closing(fundId), [api, fundId]);
  const [state] = useLoad(load);

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
      </div>
      <LoadedView state={state}>{(closing) => <History closing={closing} />}</LoadedView>
    </>
  );
}

function History({ closing }: { readonly closing: Closing }) {
  return (
    <>
      <p>
        {closing.closed_through ? `Фонд закрыт по ${formatDate(closing.closed_through)}.` : 'Закрытых дней нет.'}
        {closing.next_day && ` Следующий к закрытию — ${formatDate(closing.next_day)}.`}
      </p>
      {closing.history.length > 0 && (
        <table aria-label="Закрытые дни">
          <thead>
            <tr>
              <th>Что</th>
              <th>Дни</th>
              <th>Фонд закрыт по</th>
              <th>Кто</th>
              <th>Когда</th>
              <th>Комментарий</th>
            </tr>
          </thead>
          <tbody>
            {[...closing.history].reverse().map((row) => (
              <tr key={row.created_at + row.action + row.first_day}>
                <td>{row.action === 'close' ? 'закрыты' : 'открыты заново'}</td>
                <td>
                  {row.first_day === row.last_day
                    ? formatDate(row.first_day)
                    : `${formatDate(row.first_day)} – ${formatDate(row.last_day)}`}
                </td>
                <td>{formatDate(row.closed_through)}</td>
                <td>{row.author}</td>
                <td>{formatTime(row.created_at)}</td>
                <td>{row.comment ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
