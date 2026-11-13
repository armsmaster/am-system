import { LoadedView, useLoad } from '@am/ui';
import { useCallback, useMemo } from 'react';
import { Link } from 'react-router';

import type { RefdataApi } from '../api';
import { ClassesPanel } from '../components/ClassesPanel';
import { RatingsPanel } from '../components/RatingsPanel';
import { couponCaption, formatDate } from '../format';

/** Карточка эмитента: выпуски в справочнике, рейтинги и классификаторы эмитента. */
export function IssuerPage({
  api,
  id,
  basePath,
  canRate,
}: {
  readonly api: RefdataApi;
  readonly id: string;
  readonly basePath: string;
  readonly canRate: boolean;
}) {
  const load = useCallback(() => api.issuer(id), [api, id]);
  const [state] = useLoad(load);
  const subject = useMemo(() => ({ issuer_id: id }), [id]);

  return (
    <>
      <p>
        <Link to={`${basePath}/issuers`}>← Все эмитенты</Link>
      </p>
      <LoadedView state={state}>
        {(issuer) => (
          <article>
            <h2>{issuer.name}</h2>
            <p className="muted">
              ИНН {issuer.inn ?? '—'} · код эмитента в MOEX ISS {issuer.iss_id}
            </p>
            <section aria-label="Выпуски">
              <h3>Выпуски в справочнике</h3>
              <ul className="plain issues">
                {issuer.instruments.map((i) => (
                  <li key={i.id}>
                    <div className="cell-head">
                      <Link to={`${basePath}/instruments/${i.id}`} className="tag">
                        {i.short_name}
                      </Link>
                      <span className="mono">{i.secid}</span>
                    </div>
                    <div className="subline">
                      {couponCaption(i)}
                      {i.maturity_date && ` · погашение ${formatDate(i.maturity_date)}`}
                      {i.next_offer_date && ` · оферта ${formatDate(i.next_offer_date)}`}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
            <div className="split">
              <RatingsPanel api={api} subject={subject} canEdit={canRate} />
              <ClassesPanel api={api} subject={subject} target="issuer" canEdit={canRate} />
            </div>
          </article>
        )}
      </LoadedView>
    </>
  );
}
