import { ErrorNote, useAction } from '@am/ui';
import { useState, type FormEvent } from 'react';

import type {
  CouponKind,
  Field,
  FloaterTerms,
  Instrument,
  NominalIndexation,
  Params,
  RateKind,
  RefdataApi,
} from '../api';
import {
  COUPON_KIND_LABELS,
  FIELD_LABELS,
  INDEXATION_LABELS,
  RATE_LABELS,
  todayInMoscow,
} from '../format';

/** Поля, которые сопровождение правит в форме. Остальное приходит из ISS. */
interface Draft {
  readonly short_name: string;
  readonly maturity_date: string;
  readonly coupon_kind: CouponKind | null;
  readonly nominal_indexation: NominalIndexation;
  readonly rate: RateKind;
  readonly margin_bp: string;
  readonly capitalized: boolean;
}

function draftOf(p: Params): Draft {
  return {
    short_name: p.short_name,
    maturity_date: p.maturity_date ?? '',
    coupon_kind: p.coupon_kind,
    nominal_indexation: p.nominal_indexation,
    rate: p.floater?.rate ?? 'key_rate',
    margin_bp: p.floater?.margin_bp ?? '',
    capitalized: p.floater?.capitalized ?? false,
  };
}

/** Только то, что действительно изменилось: каждое изменённое поле закрепится. */
export function changesOf(p: Params, draft: Draft): Partial<Params> {
  const changes: Record<string, unknown> = {};
  const shortName = draft.short_name.trim();
  if (shortName !== p.short_name) {
    changes.short_name = shortName;
  }
  const maturity = draft.maturity_date || null;
  if (maturity !== p.maturity_date) {
    changes.maturity_date = maturity;
  }
  if (draft.coupon_kind !== p.coupon_kind) {
    changes.coupon_kind = draft.coupon_kind;
  }
  if (draft.nominal_indexation !== p.nominal_indexation) {
    changes.nominal_indexation = draft.nominal_indexation;
  }
  // Параметры флоатера есть только у флоатера.
  const floater: FloaterTerms | null =
    draft.coupon_kind === 'floater'
      ? { rate: draft.rate, margin_bp: draft.margin_bp.trim(), capitalized: draft.capitalized }
      : null;
  if (!sameFloater(floater, p.floater)) {
    changes.floater = floater;
  }
  return changes as Partial<Params>;
}

function sameFloater(a: FloaterTerms | null, b: FloaterTerms | null): boolean {
  if (a === null || b === null) {
    return a === b;
  }
  // 130 и 130.0 — одна и та же маржа.
  return (
    a.rate === b.rate && a.capitalized === b.capitalized && Number(a.margin_bp) === Number(b.margin_bp)
  );
}

/**
 * Правка бумаги сопровождением. Сохранение создаёт новую версию: с какой даты
 * она действует и почему — обязательны. Поправленные поля закрепляются.
 */
export function EditForm({
  instrument,
  api,
  onDone,
  today = todayInMoscow(),
}: {
  readonly instrument: Instrument;
  readonly api: RefdataApi;
  readonly onDone: () => void;
  readonly today?: string;
}) {
  const current = instrument.current;
  const isBond = instrument.kind === 'bond';
  // Новая версия не может начинаться раньше последней.
  const earliest = current.valid_from && current.valid_from > today ? current.valid_from : today;
  const [draft, setDraft] = useState<Draft>(() => draftOf(current.params));
  const [validFrom, setValidFrom] = useState(earliest);
  const [comment, setComment] = useState('');
  const [unpin, setUnpin] = useState<readonly Field[]>([]);
  const { run, error, busy } = useAction(onDone);

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));
  const toggleUnpin = (field: Field, on: boolean) =>
    setUnpin((fields) => (on ? [...fields, field] : fields.filter((f) => f !== field)));

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(() =>
      api.edit(instrument.id, {
        valid_from: validFrom,
        comment: comment.trim(),
        changes: changesOf(current.params, draft),
        unpin,
      }),
    );
  }

  return (
    <form onSubmit={submit} aria-label="Правка">
      <h3>Новая версия</h3>
      <p className="muted">
        Поправленное поле закрепляется: ежедневная сверка с ISS его не перезапишет, но покажет
        расхождение.
      </p>
      <label>
        Краткое название
        <input
          value={draft.short_name}
          onChange={(e) => set('short_name', e.target.value)}
          required
        />
      </label>
      {isBond && (
        <>
          <label>
            Дата погашения
            <input
              type="date"
              value={draft.maturity_date}
              onChange={(e) => set('maturity_date', e.target.value)}
            />
          </label>
          <label>
            Тип купона
            <select
              value={draft.coupon_kind ?? 'unknown'}
              onChange={(e) => set('coupon_kind', e.target.value as CouponKind)}
            >
              {(Object.keys(COUPON_KIND_LABELS) as CouponKind[]).map((kind) => (
                <option key={kind} value={kind}>
                  {COUPON_KIND_LABELS[kind]}
                </option>
              ))}
            </select>
          </label>
          {draft.coupon_kind === 'floater' && (
            <fieldset>
              <legend>Параметры флоатера — для расчёта спреда</legend>
              <label>
                Плавающая ставка
                <select value={draft.rate} onChange={(e) => set('rate', e.target.value as RateKind)}>
                  {(Object.keys(RATE_LABELS) as RateKind[]).map((rate) => (
                    <option key={rate} value={rate}>
                      {RATE_LABELS[rate]}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Маржа к ставке, бп
                <input
                  inputMode="decimal"
                  value={draft.margin_bp}
                  onChange={(e) => set('margin_bp', e.target.value)}
                  placeholder="например, 130"
                  required
                />
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={draft.capitalized}
                  onChange={(e) => set('capitalized', e.target.checked)}
                />
                Ставка капитализируется внутри купонного периода
              </label>
            </fieldset>
          )}
          <label>
            Индексация номинала
            <select
              value={draft.nominal_indexation}
              onChange={(e) => set('nominal_indexation', e.target.value as NominalIndexation)}
            >
              {(Object.keys(INDEXATION_LABELS) as NominalIndexation[]).map((kind) => (
                <option key={kind} value={kind}>
                  {INDEXATION_LABELS[kind]}
                </option>
              ))}
            </select>
          </label>
        </>
      )}
      {current.pinned.length > 0 && (
        <fieldset>
          <legend>Вернуть значение из ISS</legend>
          {current.pinned.map((field) => (
            <label key={field} className="checkbox">
              <input
                type="checkbox"
                checked={unpin.includes(field)}
                onChange={(e) => toggleUnpin(field, e.target.checked)}
              />
              {FIELD_LABELS[field]}
            </label>
          ))}
        </fieldset>
      )}
      <label>
        Действует с
        <input
          type="date"
          value={validFrom}
          min={current.valid_from ?? undefined}
          onChange={(e) => setValidFrom(e.target.value)}
          required
        />
      </label>
      <p className="muted hint">
        Исправляете ошибку в условиях выпуска — укажите дату начала обращения: тогда исправление
        применится и к прошлым датам.
      </p>
      <label>
        Комментарий — что и почему
        <textarea
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          minLength={3}
          maxLength={500}
          required
        />
      </label>
      <button type="submit" disabled={busy}>
        Сохранить версию
      </button>
      <ErrorNote message={error} />
    </form>
  );
}
