import { createApiClient, hasRole, type MicrofrontendProps } from '@am/platform';
import { useMemo } from 'react';
import { NavLink, Route, Routes, useParams } from 'react-router';

import { marketdataApi, type MarketdataApi } from './api';
import { FeedPage } from './pages/FeedPage';
import { OverviewPage } from './pages/OverviewPage';
import { PricePage } from './pages/PricePage';

/** Цены: обзор с разбросом, карточка цены с графиком, лента скрипта. Смотрят все роли. */
export default function MarketdataApp({ session, basePath }: MicrofrontendProps) {
  const api = useMemo(() => marketdataApi(createApiClient(session)), [session]);
  // Сервер всё равно проверит права; здесь — только чтобы не показывать лишнего.
  return <Marketdata api={api} basePath={basePath} canEdit={hasRole(session, 'trading')} />;
}

export function Marketdata({
  api,
  basePath,
  canEdit,
}: {
  readonly api: MarketdataApi;
  readonly basePath: string;
  /** Правило оценки и ручная цена — у трейдинга (решения 67 и 70). */
  readonly canEdit: boolean;
}) {
  return (
    <section>
      <h1>Цены</h1>
      {/* Ссылки — от basePath: относительные ссылки внутри маршрута «/marketdata/*»
          считались бы от текущего адреса целиком. */}
      <nav className="tabs" aria-label="Разделы цен">
        <NavLink to={basePath} end>
          Обзор
        </NavLink>
        <NavLink to={`${basePath}/feed`}>Лента скрипта</NavLink>
      </nav>
      <Routes>
        <Route index element={<OverviewPage api={api} basePath={basePath} />} />
        <Route
          path="instruments/:id"
          element={<PriceRoute api={api} basePath={basePath} canEdit={canEdit} />}
        />
        <Route path="feed" element={<FeedPage api={api} />} />
      </Routes>
    </section>
  );
}

function PriceRoute(props: {
  readonly api: MarketdataApi;
  readonly basePath: string;
  readonly canEdit: boolean;
}) {
  // Маршрут «instruments/:id» без id не совпадёт, поэтому id всегда есть.
  const id = useParams().id!;
  return <PricePage key={id} id={id} {...props} />;
}
