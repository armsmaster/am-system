import { createApiClient, hasRole, type MicrofrontendProps } from '@am/platform';
import { LoadedView, useLoad } from '@am/ui';
import { useCallback, useMemo } from 'react';
import { NavLink, Route, Routes } from 'react-router';

import { reconciliationApi, type Account, type Portfolio, type ReconciliationApi } from './api';
import { ClosingsPage } from './pages/ClosingsPage';
import { ReconciliationPage } from './pages/ReconciliationPage';
import { StatementsPage } from './pages/StatementsPage';

/**
 * Сверка с брокером и банком (design/08): сделки, движения и остатки денег фонда
 * за день против выписок. Смотрят трейдинг и сопровождение; вводит выписки,
 * принимает расхождения и подтверждает сделки — сопровождение.
 */
export default function ReconciliationApp({ session, basePath }: MicrofrontendProps) {
  const api = useMemo(() => reconciliationApi(createApiClient(session)), [session]);
  // Сервер всё равно проверит права; здесь — только чтобы не показывать лишнего.
  return (
    <Reconciliation
      api={api}
      basePath={basePath}
      isOperations={hasRole(session, 'operations')}
      isAdmin={session.roles.has('admin')}
    />
  );
}

export interface Context {
  readonly api: ReconciliationApi;
  readonly funds: readonly Portfolio[];
  readonly accounts: readonly Account[];
  readonly isOperations: boolean;
  /** Открывает закрытый день заново только администратор (решение 127). */
  readonly isAdmin: boolean;
}

export function Reconciliation({
  api,
  basePath,
  isOperations,
  isAdmin,
}: {
  readonly api: ReconciliationApi;
  readonly basePath: string;
  readonly isOperations: boolean;
  readonly isAdmin: boolean;
}) {
  const load = useCallback(() => Promise.all([api.portfolios(), api.accounts()]), [api]);
  const [state] = useLoad(load);

  return (
    <section>
      <h1>Сверка</h1>
      <nav className="tabs" aria-label="Разделы сверки">
        <NavLink to={basePath} end>
          Сверка за день
        </NavLink>
        <NavLink to={`${basePath}/statements`}>Выписки</NavLink>
        <NavLink to={`${basePath}/closings`}>Закрытые дни</NavLink>
      </nav>
      <LoadedView state={state}>
        {([portfolios, accounts]) => {
          const context: Context = {
            api,
            funds: portfolios.filter((p) => p.is_fund),
            accounts,
            isOperations,
            isAdmin,
          };
          return (
            <Routes>
              <Route index element={<ReconciliationPage context={context} />} />
              <Route path="statements" element={<StatementsPage context={context} />} />
              <Route path="closings" element={<ClosingsPage context={context} />} />
            </Routes>
          );
        }}
      </LoadedView>
    </section>
  );
}
