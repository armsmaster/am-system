import type { MicrofrontendProps } from '@am/platform';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { App, AppRoutes, type Loaders } from './App';
import { fakeSession } from './test/session';

vi.mock('@module-federation/enhanced/runtime', () => ({
  loadRemote: vi.fn(),
  registerRemotes: vi.fn(),
}));

const loaders: Loaders = {
  admin: () =>
    Promise.resolve({
      default: ({ basePath }: MicrofrontendProps) => <p>admin remote at {basePath}</p>,
    }),
};

function renderAt(path: string, session = fakeSession('admin')) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes session={session} loaders={loaders} />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('App', () => {
  it('shows the user, the roles and the home page', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)));

    renderAt('/', fakeSession('trading', 'operations'));

    expect(screen.getByText('Иван Иванов')).toBeInTheDocument();
    expect(screen.getByText('Трейдинг, Сопровождение')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Добро пожаловать/ })).toBeInTheDocument();
  });

  it('logs out on request', async () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)));
    const session = fakeSession('trading');
    renderAt('/', session);

    await userEvent.click(screen.getByRole('button', { name: 'Выйти' }));

    expect(session.logout).toHaveBeenCalledOnce();
  });

  it('mounts the admin microfrontend for an admin', async () => {
    renderAt('/admin');

    expect(await screen.findByText('admin remote at /admin')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Администрирование' })).toBeInTheDocument();
  });

  it('hides administration from everyone else', () => {
    renderAt('/admin', fakeSession('trading'));

    expect(screen.queryByRole('link', { name: 'Администрирование' })).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('только администратору');
  });

  it('shows a message for unknown pages', () => {
    renderAt('/nowhere', fakeSession());

    expect(screen.getByText('Страница не найдена.')).toBeInTheDocument();
  });

  it('works with the browser router and default loaders', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)));

    render(<App session={fakeSession()} />);

    expect(screen.getByRole('link', { name: 'Главная' })).toBeInTheDocument();
  });
});
