import { createApiClient, hasRole, type MicrofrontendProps } from '@am/platform';
import { LoadedView, useLoad } from '@am/ui';
import { useCallback, useMemo } from 'react';
import { NavLink, Route, Routes, useLocation, useParams } from 'react-router';

import { bookingApi, type BookingApi, type Instrument, type Portfolio } from './api';
import { NavPage } from './pages/NavPage';
import { OperationsPage } from './pages/OperationsPage';
import { PlanPage } from './pages/PlanPage';
import { PositionsPage } from './pages/PositionsPage';
import { ResultsPage } from './pages/ResultsPage';
import { TradesPage } from './pages/TradesPage';

/**
 * Сделки, позиции, результат, СЧА, план по расчётам и операции фонда. Сделки вводят
 * все авторизованные пользователи (решение 25), операции фонда — сопровождение.
 */
export default function BookingApp({ session, basePath }: MicrofrontendProps) {
  const api = useMemo(() => bookingApi(createApiClient(session)), [session]);
  // Сервер всё равно проверит права; здесь — только чтобы не показывать лишнего.
  return (
    <Booking
      api={api}
      basePath={basePath}
      isOperations={hasRole(session, 'operations')}
      seesLimits={hasRole(session, 'trading') || hasRole(session, 'risk')}
    />
  );
}

export interface Context {
  readonly api: BookingApi;
  readonly basePath: string;
  readonly portfolios: readonly Portfolio[];
  readonly instruments: readonly Instrument[];
  /** Сопровождение: подтверждает и правит подтверждённые сделки (решение 95), проводит операции фонда. */
  readonly isOperations: boolean;
  /** Лимиты после сделки в форме — трейдингу и риск-менеджменту (design/09, часть 9.4). */
  readonly seesLimits: boolean;
}

export function Booking({
  api,
  basePath,
  isOperations,
  seesLimits = false,
}: {
  readonly api: BookingApi;
  readonly basePath: string;
  readonly isOperations: boolean;
  readonly seesLimits?: boolean;
}) {
  const load = useCallback(() => Promise.all([api.portfolios(), api.instruments()]), [api]);
  const [state] = useLoad(load);
  const { pathname } = useLocation();
  // Открытая сделка и импорт — тот же раздел «Сделки» с панелью справа (решение 161).
  const onTrades = pathname.startsWith(`${basePath}/trades/`) || pathname === `${basePath}/import`;

  return (
    <section>
      <h1>Книга</h1>
      <nav className="tabs" aria-label="Разделы книги">
        <NavLink to={basePath} end className={({ isActive }) => (isActive || onTrades ? 'active' : undefined)}>
          Сделки
        </NavLink>
        <NavLink to={`${basePath}/positions`}>Позиции</NavLink>
        <NavLink to={`${basePath}/results`}>Результат</NavLink>
        <NavLink to={`${basePath}/nav`}>СЧА</NavLink>
        <NavLink to={`${basePath}/plan`}>План по расчётам</NavLink>
        <NavLink to={`${basePath}/operations`}>Операции фонда</NavLink>
      </nav>
      <LoadedView state={state}>
        {([portfolios, instruments]) => {
          const context: Context = { api, basePath, portfolios, instruments, isOperations, seesLimits };
          return (
            <Routes>
              {/* Один и тот же компонент: открытая сделка не перезагружает список за панелью. */}
              <Route index element={<TradesRoute context={context} />} />
              <Route path="trades/:id" element={<TradesRoute context={context} />} />
              <Route path="import" element={<TradesRoute context={context} startPanel="import" />} />
              <Route path="positions" element={<PositionsPage context={context} />} />
              <Route path="results" element={<ResultsPage context={context} />} />
              <Route path="nav" element={<NavPage context={context} />} />
              <Route path="plan" element={<PlanPage context={context} />} />
              {/* Прежний адрес «Лестницы» — тот же план по расчётам. */}
              <Route path="ladder" element={<PlanPage context={context} />} />
              <Route path="operations" element={<OperationsPage context={context} />} />
            </Routes>
          );
        }}
      </LoadedView>
    </section>
  );
}

function TradesRoute({ context, startPanel }: { readonly context: Context; readonly startPanel?: 'import' }) {
  // id есть только у адреса …/trades/:id — тогда сделка открыта в панели справа.
  const { id } = useParams();
  return <TradesPage context={context} tradeId={id} startPanel={startPanel ?? null} />;
}
