import { LoadedView, useLoad } from '@am/ui';
import { useCallback, useState } from 'react';

import type { Context } from '../App';
import type { Report } from '../api';
import { FundSelect } from '../components/FundSelect';
import { formatDate, monthText, reportMonth, STATUS_LABELS, thresholdText, usageText, valueText } from '../format';
import { download, reportCsv } from '../report';
import { BreachTable } from './BreachesPage';

/** Отчёт за месяц (п. 8.8 политики): использование на конец месяца и нарушения месяца. */
export function ReportPage({ context }: { readonly context: Context }) {
  const { api, funds, today } = context;
  const [fundId, setFundId] = useState(funds[0]!.id);
  const [month, setMonth] = useState(() => reportMonth(today));
  const load = useCallback(() => api.report(fundId, month), [api, fundId, month]);
  const [state] = useLoad(load);

  return (
    <>
      <div className="filters">
        <FundSelect funds={funds} value={fundId} onChange={setFundId} />
        <label>
          Месяц
          <input type="month" value={month} onChange={(e) => setMonth(e.target.value || reportMonth(today))} />
        </label>
      </div>
      <LoadedView state={state}>{(report) => <ReportView report={report} />}</LoadedView>
    </>
  );
}

function ReportView({ report }: { readonly report: Report }) {
  const { day } = report;
  const usage = !day.limit_set
    ? 'набора лимитов на этот день нет'
    : !day.calculated
      ? 'расчёта фонда за этот день нет'
      : null;
  return (
    <>
      <p className="notice">
        {day.fund.code}, {monthText(report.month)}: использование на {formatDate(day.date)}
        {usage ? ` — ${usage}` : ''}. Нарушения — все, что были открыты хотя бы день месяца.{' '}
        <button
          type="button"
          className="link"
          onClick={() => download(`limits-${day.fund.code}-${report.month}.csv`, reportCsv(report))}
        >
          Скачать для Excel
        </button>
      </p>
      {!usage && (
        <table className="data" aria-label="Использование на конец месяца">
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
              <tr key={`${check.code}-${index}`} className={`status-${check.status}`}>
                <td>{[check.code, check.name, check.label].filter(Boolean).join(' · ')}</td>
                <td>{valueText(check)}</td>
                <td>{thresholdText(check)}</td>
                <td>{usageText(check.usage)}</td>
                <td>{STATUS_LABELS[check.status]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h3>Нарушения за месяц</h3>
      {report.breaches.length ? (
        <BreachTable breaches={report.breaches} />
      ) : (
        <p className="muted">Нарушений за месяц не было.</p>
      )}
    </>
  );
}
