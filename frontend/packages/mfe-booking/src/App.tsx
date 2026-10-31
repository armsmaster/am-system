import { createApiClient, hasRole, type MicrofrontendProps } from '@am/platform';
import { LoadedView, useLoad } from '@am/ui';
import { useCallback, useMemo } from 'react';
import { NavLink, Route, Routes, useParams } from 'react-router';

import { bookingApi, type BookingApi, type Instrument, type Portfolio } from './api';
import { ImportPage } from './pages/ImportPage';
import { LadderPage } from './pages/LadderPage';
import { OperationsPage } from './pages/OperationsPage';
import { PositionsPage } from './pages/PositionsPage';
import { TradePage } from './pages/TradePage';
import { TradesPage } from './pages/TradesPage';

/**
 * Сделки, операции фонда, позиции и лестница. Сделки вводят все авторизованные
 * пользователи (решение 25), операции фонда — сопровождение.
 */
export default function BookingApp({ session, basePath }: MicrofrontendProps) {
  const api = useMemo(() => bookingApi(createApiClient(session)), [session]);
  // Сервер всё равно проверит права; здесь — только чтобы не показывать лишнего.
  return <Booking api={api} basePath={basePath} isOperations={hasRole(session, 'operations')} />;
}

export interface Context {
  readonly api: BookingApi;
  readonly basePath: string;
  readonly portfolios: readonly Portfolio[];
  readonly instruments: readonly Instrument[];
  /** Сопровождение: подтверждает и правит подтверждённые сделки (решение 95), проводит операции фонда. */
  readonly isOperations: boolean;
}

export function Booking({
  api,
  basePath,
  isOperations,
}: {
  readonly api: BookingApi;
  readonly basePath: string;
  readonly isOperations: boolean;
}) {
  const load = useCallback(() => Promise.all([api.portfolios(), api.instruments()]), [api]);
  const [state] = useLoad(load);

  return (
    <section>
      <h1>Сделки</h1>
      <nav className="tabs" aria-label="Разделы сделок">
        <NavLink to={basePath} end>
          Сделки за день
        </NavLink>
        <NavLink to={`${basePath}/positions`}>Позиции</NavLink>
        <NavLink to={`${basePath}/ladder`}>Лестница</NavLink>
        <NavLink to={`${basePath}/operations`}>Операции фонда</NavLink>
        <NavLink to={`${basePath}/import`}>Импорт</NavLink>
      </nav>
      <LoadedView state={state}>
        {([portfolios, instruments]) => {
          const context: Context = { api, basePath, portfolios, instruments, isOperations };
          return (
            <Routes>
              <Route index element={<TradesPage context={context} />} />
              <Route path="trades/:id" element={<TradeRoute context={context} />} />
              <Route path="positions" element={<PositionsPage context={context} />} />
              <Route path="ladder" element={<LadderPage context={context} />} />
              <Route path="operations" element={<OperationsPage context={context} />} />
              <Route path="import" element={<ImportPage context={context} />} />
            </Routes>
          );
        }}
      </LoadedView>
    </section>
  );
}

function TradeRoute({ context }: { readonly context: Context }) {
  // Маршрут «trades/:id» без id не совпадёт, поэтому id всегда есть.
  const id = useParams().id!;
  return <TradePage key={id} id={id} context={context} />;
}
