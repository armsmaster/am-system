import { LoadedView, useLoad } from '@am/ui';
import { useCallback } from 'react';

import type { Batch, MarketdataApi } from '../api';
import { ERROR_LABELS, formatTime } from '../format';
import { REFRESH_MS, useEvery } from '../refresh';

const KIND_LABELS: Readonly<Record<Batch['kind'], string>> = {
  quotes: 'снимки цен',
  bars: 'бары',
  rates: 'ставки',
};

/** Лента скрипта: что и когда пришло, что отклонено и почему. */
export function FeedPage({ api }: { readonly api: MarketdataApi }) {
  const load = useCallback(() => api.batches(), [api]);
  const [state, reload] = useLoad(load);
  useEvery(reload, REFRESH_MS);

  return (
    <>
      <p className="muted">
        Последние пакеты от скрипта цен. Формат — в спецификации docs/api/price-feed.
      </p>
      <LoadedView state={state}>{(batches) => <BatchesTable batches={batches} />}</LoadedView>
    </>
  );
}

function BatchesTable({ batches }: { readonly batches: readonly Batch[] }) {
  if (batches.length === 0) {
    return <p className="muted">Скрипт ещё ничего не присылал.</p>;
  }
  return (
    <table className="data">
      <thead>
        <tr>
          <th>Получен, МСК</th>
          <th>Что</th>
          <th>Источник</th>
          <th className="num">Принято</th>
          <th>Отклонено</th>
        </tr>
      </thead>
      <tbody>
        {batches.map((batch) => (
          <tr key={batch.id} className={batch.rejected.length ? 'wide' : undefined}>
            <td>{formatTime(batch.received_at)}</td>
            <td>{KIND_LABELS[batch.kind]}</td>
            <td>
              {batch.source}
              <div className="muted">{batch.client}</div>
            </td>
            <td className="num">{batch.accepted}</td>
            <td>
              {batch.rejected.length === 0 ? (
                '—'
              ) : (
                <ul className="plain">
                  {batch.rejected.map((item) => (
                    <li key={item.index}>
                      №{item.index + 1} {item.code}: {ERROR_LABELS[item.error] ?? item.error}
                      <div className="muted">{item.message}</div>
                    </li>
                  ))}
                </ul>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
