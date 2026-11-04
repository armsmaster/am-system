import type { Fund } from '../api';

export function FundSelect({
  funds,
  value,
  onChange,
}: {
  readonly funds: readonly Fund[];
  readonly value: string;
  readonly onChange: (id: string) => void;
}) {
  return (
    <label>
      Фонд
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {funds.map((f) => (
          <option key={f.id} value={f.id}>
            {f.code} — {f.name}
          </option>
        ))}
      </select>
    </label>
  );
}
