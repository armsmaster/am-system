import { errorMessage } from '@am/platform';
import { ErrorNote } from '@am/ui';
import { useState, type FormEvent, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router';

import { candidatesOf, type Candidate, type Lookup, type RefdataApi } from '../api';
import { couponKindLabel, formatDate, formatDecimal, KIND_LABELS } from '../format';

/**
 * Загрузка бумаги: код → поиск в MOEX ISS → предпросмотр → «Загрузить».
 * Предпросмотр показывает ровно то, что попадёт в первую версию бумаги.
 */
export function AddInstrument({
  api,
  basePath,
  canLoad,
}: {
  readonly api: RefdataApi;
  readonly basePath: string;
  readonly canLoad: boolean;
}) {
  const navigate = useNavigate();
  const [code, setCode] = useState('');
  const [preview, setPreview] = useState<Lookup | null>(null);
  const [candidates, setCandidates] = useState<readonly Candidate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function find(value: string) {
    setBusy(true);
    setError(null);
    setPreview(null);
    setCandidates([]);
    try {
      setPreview(await api.lookup(value.trim()));
    } catch (failure) {
      setError(errorMessage(failure));
      setCandidates(candidatesOf(failure));
    } finally {
      setBusy(false);
    }
  }

  async function load(lookup: Lookup) {
    setBusy(true);
    setError(null);
    try {
      const instrument = await api.load(lookup.secid);
      void navigate(`${basePath}/instruments/${instrument.id}`);
    } catch (failure) {
      setError(errorMessage(failure));
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void find(code);
  }

  function pick(candidate: Candidate) {
    setCode(candidate.secid);
    void find(candidate.secid);
  }

  return (
    <section aria-label="Добавить бумагу">
      <h2>Добавить бумагу</h2>
      <form onSubmit={submit} className="inline" aria-label="Поиск в MOEX ISS">
        <input
          aria-label="Код бумаги"
          placeholder="Тикер или ISIN, например SU26238RMFS4"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          required
        />
        <button type="submit" disabled={busy}>
          Найти в MOEX ISS
        </button>
      </form>
      <ErrorNote message={error} />
      {candidates.length > 0 && <Candidates candidates={candidates} onPick={pick} />}
      {preview && (
        <LookupPreview preview={preview}>
          {preview.instrument_id ? (
            <p>
              Бумага уже в справочнике.{' '}
              <Link to={`${basePath}/instruments/${preview.instrument_id}`}>Открыть карточку</Link>
            </p>
          ) : canLoad ? (
            <button type="button" disabled={busy} onClick={() => void load(preview)}>
              Загрузить в справочник
            </button>
          ) : (
            <p className="muted">Загрузить бумагу могут трейдинг и сопровождение.</p>
          )}
        </LookupPreview>
      )}
    </section>
  );
}

function Candidates({
  candidates,
  onPick,
}: {
  readonly candidates: readonly Candidate[];
  readonly onPick: (candidate: Candidate) => void;
}) {
  return (
    <div>
      <p>Похожие бумаги в ISS:</p>
      <ul className="plain" aria-label="Похожие бумаги">
        {candidates.map((c) => (
          <li key={c.secid}>
            <button type="button" className="link" onClick={() => onPick(c)}>
              {c.secid}
            </button>{' '}
            {c.short_name} <span className="muted">{[c.isin, c.type_name].filter(Boolean).join(', ')}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function LookupPreview({
  preview,
  children,
}: {
  readonly preview: Lookup;
  readonly children: ReactNode;
}) {
  const p = preview.params;
  return (
    <section aria-label="Предпросмотр">
      <h3>
        <span className="code">{preview.secid}</span> — {p.short_name}
      </h3>
      <dl className="facts">
        <dt>Название</dt>
        <dd>{p.name}</dd>
        <dt>ISIN</dt>
        <dd>{preview.isin ?? '—'}</dd>
        <dt>Тип</dt>
        <dd>{KIND_LABELS[preview.kind]}</dd>
        <dt>Эмитент</dt>
        <dd>{p.issuer ?? '—'}</dd>
        <dt>Начальный номинал</dt>
        <dd>{formatDecimal(p.initial_face_value, p.currency)}</dd>
        {preview.kind === 'bond' && (
          <>
            <dt>Дата погашения</dt>
            <dd>{formatDate(p.maturity_date)}</dd>
            <dt>Тип купона</dt>
            <dd>{couponKindLabel(p.coupon_kind)}</dd>
            <dt>Графики</dt>
            <dd>
              купонов: {p.coupons.length}, оферт: {p.offers.length}, амортизаций:{' '}
              {p.amortizations.length}
            </dd>
          </>
        )}
      </dl>
      {children}
    </section>
  );
}
