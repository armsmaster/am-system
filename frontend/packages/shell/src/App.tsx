import type { Session } from '@am/platform';
import type { ReactNode } from 'react';
import { BrowserRouter, Route, Routes } from 'react-router';

import { Layout } from './components/Layout';
import { Microfrontend } from './components/Microfrontend';
import {
  loadAdmin,
  loadAudit,
  loadBooking,
  loadLimits,
  loadMarketdata,
  loadReconciliation,
  loadRefdata,
  type MicrofrontendLoader,
} from './microfrontends';
import { Home } from './pages/Home';
import { accessOf } from './sections';

export interface Loaders {
  readonly admin: MicrofrontendLoader;
  readonly audit: MicrofrontendLoader;
  readonly refdata: MicrofrontendLoader;
  readonly marketdata: MicrofrontendLoader;
  readonly booking: MicrofrontendLoader;
  readonly reconciliation: MicrofrontendLoader;
  readonly limits: MicrofrontendLoader;
}

interface AppProps {
  readonly session: Session;
  readonly loaders?: Loaders;
}

const LOADERS: Loaders = {
  admin: loadAdmin,
  audit: loadAudit,
  refdata: loadRefdata,
  marketdata: loadMarketdata,
  booking: loadBooking,
  reconciliation: loadReconciliation,
  limits: loadLimits,
};

export function App({ session, loaders = LOADERS }: AppProps) {
  return (
    <BrowserRouter>
      <AppRoutes session={session} loaders={loaders} />
    </BrowserRouter>
  );
}

export function AppRoutes({ session, loaders }: Required<AppProps>) {
  const access = accessOf(session);
  const { books, limits } = access;
  const adminOnly = (page: ReactNode) =>
    access.admin ? page : <p role="alert">Раздел доступен только администратору.</p>;

  return (
    <Layout session={session} access={access}>
      <Routes>
        <Route path="/" element={<Home session={session} access={access} />} />
        <Route
          path="/refdata/*"
          element={<Microfrontend load={loaders.refdata} session={session} basePath="/refdata" />}
        />
        <Route
          path="/marketdata/*"
          element={
            <Microfrontend load={loaders.marketdata} session={session} basePath="/marketdata" />
          }
        />
        <Route
          path="/booking/*"
          element={
            books ? (
              <Microfrontend load={loaders.booking} session={session} basePath="/booking" />
            ) : (
              <p role="alert">Раздел доступен трейдингу и сопровождению.</p>
            )
          }
        />
        <Route
          path="/reconciliation/*"
          element={
            books ? (
              <Microfrontend
                load={loaders.reconciliation}
                session={session}
                basePath="/reconciliation"
              />
            ) : (
              <p role="alert">Раздел доступен трейдингу и сопровождению.</p>
            )
          }
        />
        <Route
          path="/limits/*"
          element={
            limits ? (
              <Microfrontend load={loaders.limits} session={session} basePath="/limits" />
            ) : (
              <p role="alert">Раздел доступен трейдингу, сопровождению и риск-менеджменту.</p>
            )
          }
        />
        <Route
          path="/admin/*"
          element={adminOnly(
            <Microfrontend load={loaders.admin} session={session} basePath="/admin" />,
          )}
        />
        <Route
          path="/audit/*"
          element={adminOnly(
            <Microfrontend load={loaders.audit} session={session} basePath="/audit" />,
          )}
        />
        <Route path="*" element={<p>Страница не найдена.</p>} />
      </Routes>
    </Layout>
  );
}
