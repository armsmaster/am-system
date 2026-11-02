import { ErrorNote, useAction } from '@am/ui';
import { useState, type FormEvent } from 'react';

/**
 * Отмена ошибочной записи — рейтинга или значения классификатора. Запись остаётся
 * в истории с причиной отмены, но больше не действует.
 */
export function CancelRecord({
  what,
  cancel,
  onDone,
}: {
  /** Что отменяем — для подписи кнопки: «запись от 10.11.2026: АКРА AA(RU)». */
  readonly what: string;
  readonly cancel: (comment: string) => Promise<unknown>;
  readonly onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [comment, setComment] = useState('');
  const { run, error, busy } = useAction(onDone);

  if (!open) {
    return (
      <button type="button" className="link" aria-label={`Отменить ${what}`} onClick={() => setOpen(true)}>
        Отменить
      </button>
    );
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(() => cancel(comment.trim()));
  }

  return (
    <form onSubmit={submit} aria-label={`Отмена: ${what}`}>
      <label>
        Почему запись ошибочна
        <input value={comment} onChange={(e) => setComment(e.target.value)} maxLength={500} required />
      </label>
      <button type="submit" disabled={busy || comment.trim().length < 3}>
        Отменить запись
      </button>
      <button type="button" className="link" onClick={() => setOpen(false)}>
        Не отменять
      </button>
      <ErrorNote message={error} />
    </form>
  );
}
