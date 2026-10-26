import { createApiClient, hasRole, type MicrofrontendProps } from '@am/platform';
import { useMemo } from 'react';
import { NavLink, Route, Routes } from 'react-router';

import { portfolioApi } from './api';
import { AccessPage } from './pages/AccessPage';
import { AccountsPage } from './pages/AccountsPage';
import { PortfoliosPage } from './pages/PortfoliosPage';

/** Администрирование: портфели, фонды, счета и права доступа. */
export default function AdminApp({ session, basePath }: MicrofrontendProps) {
  const api = useMemo(() => portfolioApi(createApiClient(session)), [session]);

  // Сервер всё равно проверит права; здесь — только чтобы не показывать лишнего.
  if (!hasRole(session, 'admin')) {
    return <p role="alert">Раздел доступен только администратору.</p>;
  }

  return (
    <section>
      <h1>Администрирование</h1>
      {/* Ссылки — от basePath: путь, под которым shell показывает раздел.
          Относительные ссылки внутри маршрута «/admin/*» считались бы
          от текущего адреса целиком: /admin/accounts → /admin/accounts/access. */}
      <nav className="tabs" aria-label="Разделы администрирования">
        <NavLink to={basePath} end>
          Портфели
        </NavLink>
        <NavLink to={`${basePath}/accounts`}>Счета</NavLink>
        <NavLink to={`${basePath}/access`}>Доступы</NavLink>
      </nav>
      <Routes>
        <Route index element={<PortfoliosPage api={api} />} />
        <Route path="accounts" element={<AccountsPage api={api} />} />
        <Route path="access" element={<AccessPage api={api} />} />
      </Routes>
    </section>
  );
}
