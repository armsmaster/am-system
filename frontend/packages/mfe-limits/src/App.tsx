import { createApiClient, hasRole, type MicrofrontendProps } from '@am/platform';
import { LoadedView, useLoad } from '@am/ui';
import { useCallback, useMemo } from 'react';
import { NavLink, Route, Routes } from 'react-router';

import { limitsApi, type Fund, type IsoDate, type LimitsApi } from './api';
import { names, todayInMoscow, type Names } from './format';
import { BreachesPage } from './pages/BreachesPage';
import { ReportPage } from './pages/ReportPage';
import { SetsPage } from './pages/SetsPage';
import { UsagePage } from './pages/UsagePage';

/**
 * Лимиты инвестиционной политики (design/09): использование за день, журнал нарушений,
 * отчёт за месяц и набор лимитов. Смотрят трейдинг, сопровождение и риск-менеджмент;
 * набор, вид нарушения и продление срока ведёт риск-менеджмент.
 */
export default function LimitsApp({ session, basePath }: MicrofrontendProps) {
  const api = useMemo(() => limitsApi(createApiClient(session)), [session]);
  // Сервер всё равно проверит права; здесь — только чтобы не показывать лишнего.
  return <Limits api={api} basePath={basePath} canEdit={hasRole(session, 'risk')} />;
}

export interface Context {
  readonly api: LimitsApi;
  readonly funds: readonly Fund[];
  readonly names: Names;
  /** Вести набор лимитов, вид нарушения и продление: риск-менеджмент. */
  readonly canEdit: boolean;
  readonly today: IsoDate;
}

export function Limits({
  api,
  basePath,
  canEdit,
  today = todayInMoscow(),
}: {
  readonly api: LimitsApi;
  readonly basePath: string;
  readonly canEdit: boolean;
  readonly today?: IsoDate;
}) {
  const load = useCallback(
    () => Promise.all([api.funds(), api.classifiers(), api.issuers(), api.instruments()]),
    [api],
  );
  const [state] = useLoad(load);

  return (
    <section>
      <h1>Лимиты</h1>
      <nav className="tabs" aria-label="Разделы лимитов">
        <NavLink to={basePath} end>
          Использование
        </NavLink>
        <NavLink to={`${basePath}/breaches`}>Нарушения</NavLink>
        <NavLink to={`${basePath}/report`}>Отчёт за месяц</NavLink>
        <NavLink to={`${basePath}/sets`}>Набор лимитов</NavLink>
      </nav>
      <LoadedView state={state}>
        {([funds, classifiers, issuers, instruments]) => {
          if (funds.length === 0) {
            return <p className="muted">Нет фондов, к которым у вас есть доступ.</p>;
          }
          const context: Context = {
            api,
            funds,
            names: names(classifiers, issuers, instruments),
            canEdit,
            today,
          };
          return (
            <Routes>
              <Route index element={<UsagePage context={context} />} />
              <Route path="breaches" element={<BreachesPage context={context} />} />
              <Route path="report" element={<ReportPage context={context} />} />
              <Route path="sets" element={<SetsPage context={context} />} />
            </Routes>
          );
        }}
      </LoadedView>
    </section>
  );
}
