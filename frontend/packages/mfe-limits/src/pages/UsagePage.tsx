import { LoadedView, useLoad } from '@am/ui';
import { Fragment, useCallback, useState } from 'react';
import { Link } from 'react-router';

import type { Context } from '../App';
import type { CheckUsage, FundDay } from '../api';
import { FundSelect } from '../components/FundSelect';
import { formatDate, formatNumber, STATUS_LABELS, thresholdText, usageText, valueText } from '../format';

/** Использование лимитов фонда за день: сегодня — по текущим ценам, как СЧА. */
export function UsagePage({ context }: { readonly context: Context }) {
  const { api, funds, today } = context;
  const [fundId, setFundId] = useState(funds[0]!.id);
  const [day, setDay] = useState(today);
  const load = useCallback(() => api.day(fundId, day), [api, fundId, day]);
  const [state] = useLoad(load);

  return (
    <>
      <div className="filters">
        <FundSelect funds={funds} value={fundId} onChange={setFundId} />
        <label>
          День
          <input type="date" value={day} onChange={(e) => setDay(e.target.value || today)} />
        </label>
      </div>
      <LoadedView state={state}>
        {(found) => (
          <>
            <BreachNotice day={found} />
            <DayView day={found} />
          </>
        )}
      </LoadedView>
    </>
  );
}

/** Открытые нарушения фонда в журнале — видно сразу, на главной странице раздела. */
function BreachNotice({ day }: { readonly day: FundDay }) {
  if (!day.open_breaches) {
    return null;
  }
  return (
    <p className="notice notice-error" role="status">
      {day.fund.code}: открытых нарушений в журнале — {day.open_breaches}
      {day.overdue_breaches ? `, из них просрочено — ${day.overdue_breaches}` : ''}.{' '}
      <Link to="breaches">Журнал нарушений</Link>
    </p>
  );
}

function DayView({ day }: { readonly day: FundDay }) {
  const title = `${day.fund.code}, ${formatDate(day.date)}`;
  if (!day.limit_set) {
    return (
      <p className="notice">
        {title}: набора лимитов на этот день нет. Набор заводит риск-менеджмент на вкладке «Набор лимитов».
      </p>
    );
  }
  const set = `набор — версия ${day.limit_set.version} с ${formatDate(day.limit_set.valid_from)}`;
  if (!day.calculated || !day.totals) {
    return (
      <p className="notice">
        {title}, {set}. Расчёта фонда за этот день ещё нет.
      </p>
    );
  }
  const breaches = day.checks.filter((c) => c.status === 'breach').length;
  return (
    <>
      <p className={breaches ? 'notice notice-error' : 'notice'}>
        {title}, {set}.{day.today && ' Сегодня — по текущим ценам, нарушения в журнал не попадают.'}{' '}
        {breaches ? `Нарушено проверок: ${breaches}.` : 'Нарушений нет.'}
      </p>
      <dl className="figures" aria-label="Фонд за день">
        <dt>Активы</dt>
        <dd>{formatNumber(day.totals.assets)} ₽</dd>
        <dt>Обязательства</dt>
        <dd>{formatNumber(day.totals.liabilities)} ₽</dd>
        <dt>СЧА</dt>
        <dd>{formatNumber(day.totals.nav)} ₽</dd>
        <dt>Плечо</dt>
        <dd>{formatNumber(day.totals.leverage)}</dd>
        <dt>Дюрация к активам</dt>
        <dd>{formatNumber(day.totals.duration)} года</dd>
      </dl>
      <table className="data" aria-label="Лимиты">
        <thead>
          <tr>
            <th>Лимит</th>
            <th>Значение</th>
            <th>Порог</th>
            <th>Использовано</th>
            <th>Статус</th>
          </tr>
        </thead>
        <tbody>
          {day.checks.map((check, index) => (
            <CheckRow key={`${check.code}-${index}`} check={check} />
          ))}
        </tbody>
      </table>
    </>
  );
}

function CheckRow({ check }: { readonly check: CheckUsage }) {
  const [open, setOpen] = useState(false);
  const title = [check.code, check.name, check.label].filter(Boolean).join(' · ');
  const details = check.groups.length + check.violations.length + check.missing.length > 0;
  return (
    <Fragment>
      <tr className={`status-${check.status}`}>
        <td>
          {title}
          {check.clause && <div className="muted">{check.clause}</div>}
        </td>
        <td>{valueText(check)}</td>
        <td>{thresholdText(check)}</td>
        <td>{usageText(check.usage)}</td>
        <td>
          {STATUS_LABELS[check.status]}
          {details && (
            <div>
              <button type="button" className="link" aria-expanded={open} onClick={() => setOpen(!open)}>
                {open ? 'Скрыть' : 'Подробнее'}
              </button>
            </div>
          )}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={5}>
            <Details check={check} />
          </td>
        </tr>
      )}
    </Fragment>
  );
}

function Details({ check }: { readonly check: CheckUsage }) {
  return (
    <div aria-label={`Подробно: ${check.code}`} role="group">
      {check.groups.length > 0 && (
        <ul className="plain" aria-label="Группы">
          {check.groups.map((g) => (
            <li key={g.key}>
              {g.name}: {formatNumber(g.value)}% {g.status === 'breach' && <strong>— нарушение</strong>}
            </li>
          ))}
        </ul>
      )}
      {check.violations.length > 0 && (
        <ul className="plain" aria-label="Нарушения">
          {check.violations.map((v) => (
            <li key={`${v.secid}-${v.reason}`}>
              <span className="code">{v.secid}</span> — {v.reason}
            </li>
          ))}
        </ul>
      )}
      {check.missing.length > 0 && (
        <ul className="plain" aria-label="Нет данных">
          {check.missing.map((m) => (
            <li key={`${m.secid}-${m.reason}`}>
              <span className="code">{m.secid}</span> — {m.reason}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
