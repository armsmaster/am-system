import { useCallback, useState, type FormEvent } from 'react';
import { ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';

import type { PortfolioApi } from '../api';
import { PortfolioDetails } from '../components/PortfolioDetails';
import { PortfolioTree } from '../components/PortfolioTree';

export function PortfoliosPage({ api }: { readonly api: PortfolioApi }) {
  const load = useCallback(() => Promise.all([api.portfolios(), api.accounts()]), [api]);
  const [state, reload] = useLoad(load);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  return (
    <LoadedView state={state}>
      {([portfolios, accounts]) => {
        const selected = portfolios.find((p) => p.id === selectedId);
        return (
          <div className="split">
            <section aria-label="Структура">
              <PortfolioTree
                portfolios={portfolios}
                selectedId={selectedId}
                onSelect={setSelectedId}
              />
              <NewRootForm api={api} onDone={reload} />
            </section>
            <section aria-label="Карточка портфеля">
              {selected ? (
                <PortfolioDetails
                  key={selected.id}
                  portfolio={selected}
                  portfolios={portfolios}
                  accounts={accounts}
                  api={api}
                  onChanged={reload}
                />
              ) : (
                <p className="muted">Выберите портфель в дереве.</p>
              )}
            </section>
          </div>
        );
      }}
    </LoadedView>
  );
}

/** Фонд или представление верхнего уровня. */
function NewRootForm({ api, onDone }: { readonly api: PortfolioApi; readonly onDone: () => void }) {
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [isFund, setIsFund] = useState(true);
  const { run, error, busy } = useAction(onDone);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const created = await run(() =>
      api.createPortfolio({ code, name, kind: 'compound', is_fund: isFund }),
    );
    if (created) {
      setCode('');
      setName('');
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} aria-label="Новый фонд или представление">
      <h3>Новый фонд или представление</h3>
      <label>
        Код
        <input value={code} onChange={(e) => setCode(e.target.value)} required />
      </label>
      <label>
        Название
        <input value={name} onChange={(e) => setName(e.target.value)} required />
      </label>
      <label className="checkbox">
        <input type="checkbox" checked={isFund} onChange={(e) => setIsFund(e.target.checked)} />
        Это фонд
      </label>
      <button type="submit" disabled={busy}>
        Создать
      </button>
      <ErrorNote message={error} />
    </form>
  );
}
