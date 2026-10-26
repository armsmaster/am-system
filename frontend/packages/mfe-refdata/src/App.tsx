import { createApiClient, hasRole, type MicrofrontendProps } from '@am/platform';
import { useMemo } from 'react';
import { NavLink, Route, Routes, useParams } from 'react-router';

import { refdataApi, type RefdataApi } from './api';
import { CalendarPage } from './pages/CalendarPage';
import { InstrumentPage } from './pages/InstrumentPage';
import { InstrumentsPage } from './pages/InstrumentsPage';

/** Что пользователь может делать в справочнике. Смотреть могут все. */
export interface Rights {
  /** Загрузить бумагу из ISS: трейдинг и сопровождение. */
  readonly canLoad: boolean;
  /** Править бумагу, сверять с ISS, вести календарь: сопровождение. */
  readonly canEdit: boolean;
}

/** Справочник: бумаги из MOEX ISS, их версии и торговый календарь. */
export default function RefdataApp({ session, basePath }: MicrofrontendProps) {
  const api = useMemo(() => refdataApi(createApiClient(session)), [session]);
  // Сервер всё равно проверит права; здесь — только чтобы не показывать лишнего.
  const rights: Rights = {
    canLoad: hasRole(session, 'trading') || hasRole(session, 'operations'),
    canEdit: hasRole(session, 'operations'),
  };
  return <Refdata api={api} basePath={basePath} rights={rights} />;
}

export function Refdata({
  api,
  basePath,
  rights,
}: {
  readonly api: RefdataApi;
  readonly basePath: string;
  readonly rights: Rights;
}) {
  return (
    <section>
      <h1>Справочник</h1>
      {/* Ссылки — от basePath, как в админке: относительные ссылки внутри
          маршрута «/refdata/*» считались бы от текущего адреса целиком. */}
      <nav className="tabs" aria-label="Разделы справочника">
        <NavLink to={basePath} end>
          Бумаги
        </NavLink>
        <NavLink to={`${basePath}/calendar`}>Торговый календарь</NavLink>
      </nav>
      <Routes>
        <Route
          index
          element={<InstrumentsPage api={api} basePath={basePath} canLoad={rights.canLoad} />}
        />
        <Route
          path="instruments/:id"
          element={<InstrumentRoute api={api} basePath={basePath} canEdit={rights.canEdit} />}
        />
        <Route path="calendar" element={<CalendarPage api={api} canEdit={rights.canEdit} />} />
      </Routes>
    </section>
  );
}

function InstrumentRoute(props: {
  readonly api: RefdataApi;
  readonly basePath: string;
  readonly canEdit: boolean;
}) {
  // Маршрут «instruments/:id» без id не совпадёт, поэтому id всегда есть.
  const id = useParams().id!;
  return <InstrumentPage key={id} id={id} {...props} />;
}
