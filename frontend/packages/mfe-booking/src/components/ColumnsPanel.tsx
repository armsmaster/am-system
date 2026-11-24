import type { Classifier } from '../api';
import {
  activePreset,
  classColumn,
  columnLabel,
  moveColumn,
  PART_LABELS,
  PERIOD_KEYS,
  PERIOD_SHORT,
  periodColumn,
  POSITION_COLUMNS,
  PRESET_LABELS,
  RISK_COLUMNS,
  toggleColumn,
  type ColumnOption,
  type Preset,
} from '../positions';

/**
 * Панель «Колонки таблицы» (решение 164): готовые наборы, колонки позиции, матрица
 * «результат и реализованный × период», риск и справочник, порядок колонок.
 */
export function ColumnsPanel({
  shown,
  classifiers,
  onChange,
  onPreset,
  onReset,
}: {
  readonly shown: readonly string[];
  readonly classifiers: readonly Classifier[];
  readonly onChange: (columns: string[]) => void;
  readonly onPreset: (preset: Preset) => void;
  readonly onReset: () => void;
}) {
  const active = activePreset(shown, classifiers);
  const toggle = (id: string) => onChange(toggleColumn(shown, id, classifiers));
  const pill = (option: ColumnOption) => (
    <button
      key={option.id}
      type="button"
      className="toggle-pill"
      aria-pressed={shown.includes(option.id)}
      onClick={() => toggle(option.id)}
    >
      {option.label}
    </button>
  );
  const references = [...RISK_COLUMNS, ...classifiers.map((c) => ({ id: classColumn(c), label: c.name }))];

  return (
    <div className="columns-panel">
      <h3>Готовые наборы</h3>
      <div className="toggle-row" role="group" aria-label="Готовые наборы">
        {(Object.keys(PRESET_LABELS) as Preset[]).map((preset) => (
          <button
            key={preset}
            type="button"
            className="toggle-pill"
            aria-pressed={preset === active}
            onClick={() => onPreset(preset)}
          >
            {PRESET_LABELS[preset]}
          </button>
        ))}
      </div>

      <h3>Позиция</h3>
      <div className="toggle-row" role="group" aria-label="Позиция">
        {POSITION_COLUMNS.map(pill)}
      </div>

      <h3>Результат и реализованный PL по периодам</h3>
      <table className="period-matrix" aria-label="Результат по периодам">
        <thead>
          <tr>
            <th />
            {PERIOD_KEYS.map((period) => (
              <th key={period} scope="col">
                {PERIOD_SHORT[period]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {(['result', 'realized'] as const).map((part) => (
            <tr key={part}>
              <th scope="row">{PART_LABELS[part]}</th>
              {PERIOD_KEYS.map((period) => {
                const id = periodColumn(part, period);
                const on = shown.includes(id);
                return (
                  <td key={period}>
                    <button
                      type="button"
                      className="matrix-cell"
                      aria-pressed={on}
                      aria-label={columnLabel(id, classifiers)}
                      onClick={() => toggle(id)}
                    >
                      {on ? '✓' : '—'}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="subline">«Мес.», «Кв.», «Год» — с начала месяца, квартала, года.</p>

      <h3>Риск и справочник</h3>
      <div className="toggle-row" role="group" aria-label="Риск и справочник">
        {references.map(pill)}
      </div>

      <h3>Порядок колонок</h3>
      {shown.length === 0 ? (
        <p className="muted">Колонки не выбраны — в таблице только бумаги.</p>
      ) : (
        <ol className="column-order" aria-label="Порядок колонок">
          {shown.map((id, index) => {
            const label = columnLabel(id, classifiers);
            return (
              <li key={id}>
                <span>{label}</span>
                <button
                  type="button"
                  className="icon-button"
                  aria-label={`Левее: ${label}`}
                  disabled={index === 0}
                  onClick={() => onChange(moveColumn(shown, id, -1))}
                >
                  ↑
                </button>
                <button
                  type="button"
                  className="icon-button"
                  aria-label={`Правее: ${label}`}
                  disabled={index === shown.length - 1}
                  onClick={() => onChange(moveColumn(shown, id, 1))}
                >
                  ↓
                </button>
              </li>
            );
          })}
        </ol>
      )}
      <p className="subline">
        Сохраняется в этом браузере ·
        <button type="button" className="link-button" onClick={onReset}>
          Вернуть по умолчанию
        </button>
      </p>
    </div>
  );
}
