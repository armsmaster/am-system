import { ApiError, errorMessage } from '@am/platform';
import { useState, type ChangeEvent } from 'react';
import { Link } from 'react-router';

import type { Context } from '../App';
import type { ImportReport, ImportResult } from '../api';
import { QuikImport } from '../components/QuikImport';

const RESULT_LABELS: Readonly<Record<ImportResult, string>> = {
  created: 'новая сделка',
  amended: 'новая версия',
  cancelled: 'отменена',
  unchanged: 'без изменений',
};

type State =
  | { readonly status: 'idle' }
  | { readonly status: 'busy'; readonly file: string }
  | { readonly status: 'done'; readonly file: string; readonly report: ImportReport }
  | { readonly status: 'failed'; readonly file: string; readonly message: string; readonly details: string[] };

/** Ошибки JSON Schema из ответа 422: «сделка 3, price: …». */
function schemaErrors(error: unknown): string[] {
  if (!(error instanceof ApiError) || error.status !== 422) {
    return [];
  }
  const detail = (error.body as { detail?: unknown } | null)?.detail;
  if (!Array.isArray(detail)) {
    return [];
  }
  return detail.map((item: { loc?: unknown[]; msg?: string }) => {
    const [, ...path] = item.loc ?? [];
    const where = path
      .map((part, index) => (typeof part === 'number' && path[index - 1] === 'trades' ? `сделка ${part + 1}` : part))
      .filter((part) => part !== 'trades')
      .join(', ');
    return `${where || 'пакет'}: ${item.msg ?? ''}`;
  });
}

type Source = 'quik' | 'json';

/** Импорт сделок из файла: отчёт QUIK (решение 151) или пакет JSON (docs/api/trade-import). */
export function ImportPage({ context }: { readonly context: Context }) {
  const [source, setSource] = useState<Source>('quik');
  const tab = (value: Source, label: string) => (
    <button type="button" role="tab" aria-selected={source === value} onClick={() => setSource(value)}>
      {label}
    </button>
  );
  return (
    <>
      <div className="tabs" role="tablist" aria-label="Откуда сделки">
        {tab('quik', 'Отчёт QUIK')}
        {tab('json', 'Пакет JSON')}
      </div>
      {source === 'quik' ? <QuikImport context={context} /> : <JsonImport context={context} />}
    </>
  );
}

/**
 * Импорт сделок из файла по спецификации docs/api/trade-import — тот же пакет,
 * что присылает внешняя система. Ответ: что принято, что отклонено и почему.
 */
function JsonImport({ context }: { readonly context: Context }) {
  const [state, setState] = useState<State>({ status: 'idle' });

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) {
      return;
    }
    setState({ status: 'busy', file: file.name });
    let body: unknown;
    try {
      body = JSON.parse(await file.text());
    } catch {
      setState({ status: 'failed', file: file.name, message: 'Файл — не JSON: проверьте его содержимое.', details: [] });
      return;
    }
    try {
      const report = await context.api.importTrades(body);
      setState({ status: 'done', file: file.name, report });
    } catch (failure) {
      const details = schemaErrors(failure);
      setState({
        status: 'failed',
        file: file.name,
        message: details.length
          ? 'Файл не соответствует спецификации — не принято ничего.'
          : errorMessage(failure),
        details,
      });
    }
  }

  return (
    <>
      <p>
        Пакет сделок JSON по спецификации <span className="code">docs/api/trade-import</span>. Повторная
        загрузка безопасна: сделки с тем же источником и номером не задвоятся.
      </p>
      <form className="inline" aria-label="Загрузка сделок" onSubmit={(e) => e.preventDefault()}>
        <label>
          Файл со сделками
          <input type="file" accept=".json,application/json" onChange={(e) => void upload(e)} />
        </label>
      </form>
      {state.status === 'busy' && <p className="muted">Загружаем {state.file}…</p>}
      {state.status === 'failed' && (
        <div role="alert" className="notice notice-error">
          <p>
            {state.file}: {state.message}
          </p>
          {state.details.length > 0 && (
            <ul>
              {state.details.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {state.status === 'done' && <ReportView file={state.file} report={state.report} basePath={context.basePath} />}
    </>
  );
}

function ReportView({
  file,
  report,
  basePath,
}: {
  readonly file: string;
  readonly report: ImportReport;
  readonly basePath: string;
}) {
  return (
    <>
      <p className="notice">
        {file}: новых сделок — {report.created}, новых версий — {report.amended}, отменено —{' '}
        {report.cancelled}, без изменений — {report.unchanged}, отклонено — {report.rejected.length}.
      </p>
      {report.rejected.length > 0 && (
        <table className="data" aria-label="Отклонено">
          <thead>
            <tr>
              <th className="num">№ в пакете</th>
              <th>Номер сделки</th>
              <th>Почему отклонена</th>
            </tr>
          </thead>
          <tbody>
            {report.rejected.map((r) => (
              <tr key={r.index}>
                <td className="num">{r.index + 1}</td>
                <td className="code">{r.external_id}</td>
                <td className="danger">{r.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {report.trades.length > 0 && (
        <table className="data" aria-label="Принято">
          <thead>
            <tr>
              <th className="num">№ в пакете</th>
              <th>Номер сделки</th>
              <th>Что сделано</th>
              <th>Предупреждения</th>
            </tr>
          </thead>
          <tbody>
            {report.trades.map((t) => (
              <tr key={t.index}>
                <td className="num">{t.index + 1}</td>
                <td>
                  <Link to={`${basePath}/trades/${t.trade_id}`} className="code">
                    {t.external_id}
                  </Link>
                </td>
                <td>{RESULT_LABELS[t.result]}</td>
                <td>{t.warnings.join(' ') || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
