import { ErrorNote, useAction } from '@am/ui';
import { useState, type FormEvent } from 'react';

import type { MarketdataApi } from '../api';

/** Ручная цена трейдинга: до конца торгового дня — и только (решение 70). */
export function ManualPriceForm({
  id,
  api,
  onDone,
}: {
  readonly id: string;
  readonly api: MarketdataApi;
  readonly onDone: () => void;
}) {
  const [value, setValue] = useState('');
  const [comment, setComment] = useState('');
  const { run, error, busy } = useAction(onDone);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const saved = await run(() =>
      api.manualPrice(id, { value: value.trim().replace(',', '.'), comment: comment.trim() }),
    );
    if (saved) {
      setValue('');
      setComment('');
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} aria-label="Ручная цена">
      <h3>Ручная цена</h3>
      <p className="muted">
        Заменяет правило оценки до конца торгового дня. Ошиблись — введите новую цену: действует
        последняя.
      </p>
      <label>
        Цена
        <input
          inputMode="decimal"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="например, 99,50"
          required
        />
      </label>
      <label>
        Комментарий — откуда цена
        <input
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          minLength={3}
          maxLength={500}
          required
        />
      </label>
      <button type="submit" disabled={busy}>
        Ввести цену
      </button>
      <ErrorNote message={error} />
    </form>
  );
}
