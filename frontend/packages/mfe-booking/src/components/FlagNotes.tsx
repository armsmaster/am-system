import type { ValuationFlag } from '../api';
import { FLAG_LABELS, incompleteFlags } from '../format';

/** Купон линкера по условиям выпуска — пометка до подтверждения ISS, не тревога (решение 160). */
export const DUE_NOTE =
  'Купон или погашение линкера к получению посчитаны по условиям выпуска — ISS подтвердит сумму в день выплаты.';

/**
 * Почему оценка неполная — предупреждением; купон линкера по условиям выпуска —
 * спокойной строкой отдельно.
 */
export function FlagNotes({
  flags,
  lead = 'Оценка неполная',
}: {
  readonly flags: readonly ValuationFlag[];
  readonly lead?: string;
}) {
  const incomplete = incompleteFlags(flags);
  return (
    <>
      {incomplete.length > 0 && (
        <p className="notice notice-warning">
          {lead}: {incomplete.map((f) => FLAG_LABELS[f]).join('; ')}.
        </p>
      )}
      {flags.includes('due_estimated') && <p className="muted">{DUE_NOTE}</p>}
    </>
  );
}
