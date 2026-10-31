import { createApiClient, hasRole, type MicrofrontendProps } from '@am/platform';
import { LoadedView, useLoad } from '@am/ui';
import { useCallback, useMemo } from 'react';
import { NavLink, Route, Routes, useParams } from 'react-router';

import { bookingApi, type BookingApi, type Instrument, type Portfolio } from './api';
import { PositionsPage } from './pages/PositionsPage';
import { TradePage } from './pages/TradePage';
import { TradesPage } from './pages/TradesPage';

/** Сделки и позиции. Сделки вводят все авторизованные пользователи (решение 25). */
export default function BookingApp({ session, basePath }: MicrofrontendProps) {
  const api = useMemo(() => bookingApi(createApiClient(session)), [session]);
  // Сервер всё равно проверит права; здесь — только чтобы не показывать лишнего.
  return <Booking api={api} basePath={basePath} canConfirm={hasRole(session, 'operations')} />;
}

export interface Context {
  readonly api: BookingApi;
  readonly basePath: string;
  readonly portfolios: readonly Portfolio[];
  readonly instruments: readonly Instrument[];
  /** Подтверждает и правит подтверждённые сделки сопровождение (решение 95). */
  readonly canConfirm: boolean;
}

export function Booking({
  api,
  basePath,
  canConfirm,
}: {
  readonly api: BookingApi;
  readonly basePath: string;
  readonly canConfirm: boolean;
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
      </nav>
      <LoadedView state={state}>
        {([portfolios, instruments]) => {
          const context: Context = { api, basePath, portfolios, instruments, canConfirm };
          return (
            <Routes>
              <Route index element={<TradesPage context={context} />} />
              <Route path="trades/:id" element={<TradeRoute context={context} />} />
              <Route path="positions" element={<PositionsPage context={context} />} />
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
