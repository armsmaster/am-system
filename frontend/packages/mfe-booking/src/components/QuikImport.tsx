import { errorMessage } from '@am/platform';
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router';

import type { Context } from '../App';
import type { QuikLine, QuikReport } from '../api';
import { decimalInput, formatDate, formatMoney, formatPrice, formatQuantity, formatTime } from '../format';

const RESULT_LABELS: Readonly<Record<NonNullable<QuikLine['result']>, string>> = {
  created: 'новая сделка',
  amended: 'новая версия',
  unchanged: 'без изменений',
};

type State =
  | { readonly status: 'idle' }
  | { readonly status: 'busy' }
  | { readonly status: 'done'; readonly report: QuikReport }
  | { readonly status: 'failed'; readonly message: string };

/** Файл в base64 — так его принимает сервис: отчёт весит десятки килобайт. */
async function base64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  for (let start = 0; start < bytes.length; start += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
  }
  return btoa(binary);
}

/** «0.012500» → «0,0125%». */
function percent(value: string): string {
  return `${formatPrice(value)}%`;
}

/**
 * Загрузка сделок из отчёта QUIK «Отчёт по всем сделкам клиента» (решение 151).
 * Сначала проверка — что станет с каждой сделкой и какая у неё комиссия, потом
 * загрузка. Комиссию система считает по тарифу брокера на счёт портфеля или по
 * ставкам, заданным на эту загрузку.
 */
export function QuikImport({ context }: { readonly context: Context }) {
  const portfolios = context.portfolios.filter((p) => p.kind === 'physical');
  const [portfolioId, setPortfolioId] = useState(portfolios[0]?.id ?? '');
  const [file, setFile] = useState<{ readonly name: string; readonly data: string } | null>(null);
  const [outright, setOutright] = useState('');
  const [repo, setRepo] = useState('');
  const [state, setState] = useState<State>({ status: 'idle' });

  // Изменили файл, портфель или ставки — прежняя проверка уже не про них.
  function changed<T>(set: (value: T) => void) {
    return (value: T) => {
      set(value);
      setState({ status: 'idle' });
    };
  }

  async function choose(chosen: File | undefined) {
    setState({ status: 'idle' });
    setFile(chosen ? { name: chosen.name, data: await base64(chosen) } : null);
  }

  async function run(apply: boolean) {
    if (!file) {
      return;
    }
    setState({ status: 'busy' });
    try {
      const report = await context.api.importQuik({
        file: file.data,
        portfolio_id: portfolioId,
        outright_rate: outright.trim() ? decimalInput(outright) : null,
        repo_rate: repo.trim() ? decimalInput(repo) : null,
        apply,
      });
      setState({ status: 'done', report });
    } catch (failure) {
      setState({ status: 'failed', message: errorMessage(failure) });
    }
  }

  function check(event: FormEvent) {
    event.preventDefault();
    void run(false);
  }

  if (portfolios.length === 0) {
    return <p className="muted">Нет портфелей, в которые брокер заводит сделки, — загружать отчёт некуда.</p>;
  }
  const report = state.status === 'done' ? state.report : null;
  const toLoad = report && !report.applied ? report.lines.filter((l) => l.result && l.result !== 'unchanged') : [];
  const newInstruments = report?.new_instruments ?? [];

  return (
    <>
      <p>
        Выгрузите в QUIK «Отчёт по всем сделкам клиента» в формате Excel и загрузите его сюда. Комиссии в отчёте
        нет — система посчитает её по тарифу брокера на счёт портфеля: купля-продажа — % от оборота с НКД, РЕПО —
        % годовых от суммы первой части. Чтобы взять другие ставки, задайте их на эту загрузку. Повторная загрузка
        безопасна: ключ — номер сделки.
      </p>
      <form onSubmit={check} aria-label="Загрузка из отчёта QUIK">
        <div className="fields">
          <label>
            Отчёт QUIK
            <input type="file" accept=".xls,.htm,.html" onChange={(e) => void choose(e.target.files?.[0])} />
          </label>
          <label>
            Портфель
            <select value={portfolioId} onChange={(e) => changed(setPortfolioId)(e.target.value)}>
              {portfolios.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.code} — {p.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Купля-продажа, % от оборота с НКД
            <input
              value={outright}
              inputMode="decimal"
              placeholder="по тарифу счёта"
              onChange={(e) => changed(setOutright)(e.target.value)}
            />
          </label>
          <label>
            РЕПО, % годовых
            <input
              value={repo}
              inputMode="decimal"
              placeholder="по тарифу счёта"
              onChange={(e) => changed(setRepo)(e.target.value)}
            />
          </label>
        </div>
        <button type="submit" disabled={!file || state.status === 'busy'}>
          Проверить
        </button>
      </form>
      {state.status === 'busy' && <p className="muted">Читаем {file?.name}…</p>}
      {state.status === 'failed' && (
        <p role="alert" className="notice notice-error">
          {file?.name}: {state.message}
        </p>
      )}
      {report && (
        <>
          <Summary report={report} />
          {newInstruments.length > 0 && (
            <div role="alert" className="notice notice-error">
              <p>
                В отчёте бумаги, которых нет в справочнике. Сначала заведите их в разделе{' '}
                <Link to="/refdata">«Справочник»</Link>, потом проверьте и загрузите отчёт снова.
              </p>
              <ul aria-label="Новые бумаги">
                {newInstruments.map((i) => (
                  <li key={i.code}>
                    <span className="code">{i.code}</span> {i.name}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {!report.applied && (
            <p>
              <button
                type="button"
                disabled={toLoad.length === 0 || newInstruments.length > 0}
                onClick={() => void run(true)}
              >
                Загрузить
              </button>{' '}
              {toLoad.length === 0 && <span className="muted">Загружать нечего: новых и изменённых сделок нет.</span>}
            </p>
          )}
          <Lines report={report} basePath={context.basePath} />
        </>
      )}
    </>
  );
}

function Summary({ report }: { readonly report: QuikReport }) {
  const count = (result: QuikLine['result']) => report.lines.filter((l) => l.result === result).length;
  const rejected = report.lines.filter((l) => l.error).length;
  const period =
    report.period_from && report.period_till
      ? `Отчёт за ${formatDate(report.period_from)} — ${formatDate(report.period_till)}`
      : 'Отчёт';
  const tariff = report.tariff;
  return (
    <>
      <p className="notice">
        {period}
        {report.client_code && `, код клиента ${report.client_code}`}.{' '}
        {report.applied ? 'Загружено' : 'Проверка, ещё не загружено'}: новых сделок — {count('created')}, новых версий
        — {count('amended')}, без изменений — {count('unchanged')}, отклонено — {rejected}.
      </p>
      <p className="muted">
        {tariff
          ? `Тариф счёта с ${formatDate(tariff.valid_from)}: купля-продажа — ${percent(tariff.outright_rate)} ` +
            `от оборота с НКД, РЕПО — ${percent(tariff.repo_rate)} годовых.`
          : 'Тарифа брокера у счёта нет — задайте ставки на загрузку или попросите администратора завести тариф.'}
      </p>
    </>
  );
}

function deal(line: QuikLine): string {
  if (line.kind === 'repo') {
    return line.side === 'sell' ? 'прямое РЕПО' : 'обратное РЕПО';
  }
  return line.side === 'sell' ? 'продажа' : 'покупка';
}

function Lines({ report, basePath }: { readonly report: QuikReport; readonly basePath: string }) {
  return (
    <table className="data" aria-label="Сделки отчёта">
      <thead>
        <tr>
          <th className="num">Строка</th>
          <th>Номер сделки</th>
          <th>Заключена</th>
          <th>Бумага</th>
          <th>Сделка</th>
          <th className="num">Кол-во</th>
          <th className="num">Цена</th>
          <th className="num">Сумма, ₽</th>
          <th className="num">Комиссия, ₽</th>
          <th>{report.applied ? 'Результат' : 'Что будет'}</th>
        </tr>
      </thead>
      <tbody>
        {report.lines.map((line) => (
          <tr key={`${line.line}-${line.external_id}`}>
            <td className="num">{line.line}</td>
            <td className="code">
              {line.trade_id && report.applied ? (
                <Link to={`${basePath}/trades/${line.trade_id}`}>{line.external_id}</Link>
              ) : (
                line.external_id
              )}
            </td>
            <td>{line.trade_at ? formatTime(line.trade_at) : '—'}</td>
            <td>
              {line.code && <span className="code">{line.code}</span>} {line.name}
            </td>
            <td>{line.side ? deal(line) : '—'}</td>
            <td className="num">{line.quantity === null ? '—' : formatQuantity(line.quantity)}</td>
            <td className="num">{formatPrice(line.price)}</td>
            <td className="num">{formatMoney(line.amount)}</td>
            <td className="num">
              {formatMoney(line.fee)}
              {line.fee_rate && <span className="muted"> ({percent(line.fee_rate)})</span>}
            </td>
            <td className={line.error ? 'danger' : undefined}>
              {line.result ? RESULT_LABELS[line.result] : line.message}
              {line.warnings.length > 0 && <span className="muted"> {line.warnings.join(' ')}</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
