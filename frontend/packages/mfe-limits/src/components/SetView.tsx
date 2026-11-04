import type { SetBody } from '../api';
import { checkText, formatDate, ratingText, type Names } from '../format';

/** Набор лимитов словами: документ, правило рейтинга, лимиты и их проверки. */
export function SetView({ body, names }: { readonly body: SetBody; readonly names: Names }) {
  const { document } = body;
  return (
    <article aria-label="Набор лимитов словами">
      <h3>
        {document.name}
        {document.version && `, версия ${document.version}`}
        {document.approved_on && ` от ${formatDate(document.approved_on)}`}
      </h3>
      <h4>Рейтинг для лимитов</h4>
      <ul className="plain">
        {ratingText(body.settings, names).map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <h4>Лимиты</h4>
      <ul className="plain">
        {body.limits.map((limit) => (
          <li key={limit.code}>
            <strong>
              {limit.code} · {limit.name}
            </strong>
            {limit.clause && <span className="muted"> — {limit.clause}</span>}
            <ul>
              {limit.checks.map((check, index) => (
                <li key={index}>
                  {check.label && <em>{check.label}: </em>}
                  {checkText(check, names)}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </article>
  );
}
