import { LoadedView, useLoad } from '@am/ui';
import { useCallback, useState } from 'react';
import { Link } from 'react-router';

import type { RefdataApi } from '../api';

/** Эмитенты бумаг справочника: их заводит загрузка бумаги из MOEX ISS. */
export function IssuersPage({ api, basePath }: { readonly api: RefdataApi; readonly basePath: string }) {
  const [text, setText] = useState('');
  const query = text.trim();
  const load = useCallback(() => api.issuers(query), [api, query]);
  const [state] = useLoad(load);

  return (
    <section aria-label="Эмитенты">
      <h2>Эмитенты</h2>
      <p className="muted">
        Эмитент появляется вместе с первой его бумагой, загруженной из MOEX ISS: справочник берёт код эмитента,
        название и ИНН. Рейтинги и классификаторы эмитента ведёт риск-менеджмент.
      </p>
      <input
        type="search"
        aria-label="Поиск эмитента"
        placeholder="Название или ИНН"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <LoadedView state={state}>
        {(items) =>
          items.length === 0 ? (
            <p className="muted">
              {query
                ? 'Ничего не нашлось.'
                : 'Эмитентов пока нет. У бумаг, загруженных раньше, эмитент появится при сверке с MOEX ISS — утром или по кнопке в карточке бумаги.'}
            </p>
          ) : (
            <table className="data">
              <thead>
                <tr>
                  <th>Эмитент</th>
                  <th>ИНН</th>
                  <th>Код ISS</th>
                  <th>Бумаг</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <Link to={`${basePath}/issuers/${item.id}`}>{item.name}</Link>
                    </td>
                    <td>{item.inn ?? '—'}</td>
                    <td>{item.iss_id}</td>
                    <td>{item.instruments}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
      </LoadedView>
    </section>
  );
}
