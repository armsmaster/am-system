import { hasRole, ROLE_LABELS, type Session } from '@am/platform';
import type { ReactNode } from 'react';
import { BrowserRouter, NavLink, Route, Routes } from 'react-router';

import { Microfrontend } from './components/Microfrontend';
import {
  loadAdmin,
  loadAudit,
  loadMarketdata,
  loadRefdata,
  type MicrofrontendLoader,
} from './microfrontends';
import { Home } from './pages/Home';

export interface Loaders {
  readonly admin: MicrofrontendLoader;
  readonly audit: MicrofrontendLoader;
  readonly refdata: MicrofrontendLoader;
  readonly marketdata: MicrofrontendLoader;
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
};

export function App({ session, loaders = LOADERS }: AppProps) {
  return (
    <BrowserRouter>
      <AppRoutes session={session} loaders={loaders} />
    </BrowserRouter>
  );
}

export function AppRoutes({ session, loaders }: Required<AppProps>) {
  const isAdmin = hasRole(session, 'admin');
  const adminOnly = (page: ReactNode) =>
    isAdmin ? page : <p role="alert">Раздел доступен только администратору.</p>;

  return (
    <Layout session={session} isAdmin={isAdmin}>
      <Routes>
        <Route path="/" element={<Home session={session} />} />
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

function Layout({
  session,
  isAdmin,
  children,
}: {
  readonly session: Session;
  readonly isAdmin: boolean;
  readonly children: ReactNode;
}) {
  const roles = [...session.roles].map((role) => ROLE_LABELS[role]).join(', ');

  return (
    <div className="layout">
      <header className="header">
        <span className="brand">AM System</span>
        <nav className="nav">
          <NavLink to="/" end>
            Главная
          </NavLink>
          <NavLink to="/refdata">Справочник</NavLink>
          <NavLink to="/marketdata">Цены</NavLink>
          {isAdmin && <NavLink to="/admin">Администрирование</NavLink>}
          {isAdmin && <NavLink to="/audit">Журнал</NavLink>}
        </nav>
        <div className="user">
          <span>{session.displayName}</span>
          {roles && <span className="muted">{roles}</span>}
          <button type="button" onClick={() => session.logout()}>
            Выйти
          </button>
        </div>
      </header>
      <main className="content">{children}</main>
    </div>
  );
}
