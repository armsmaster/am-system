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

const remote = (name: string) => () =>
  Promise.resolve({
    default: ({ basePath }: MicrofrontendProps) => (
      <p>
        {name} remote at {basePath}
      </p>
    ),
  });

const loaders: Loaders = {
  admin: remote('admin'),
  audit: remote('audit'),
  refdata: remote('refdata'),
  marketdata: remote('marketdata'),
  booking: remote('booking'),
  reconciliation: remote('reconciliation'),
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

  it.each([
    ['/admin', 'Администрирование'],
    ['/audit', 'Журнал'],
  ])('mounts the %s microfrontend for an admin', async (path, link) => {
    renderAt(path);

    expect(await screen.findByText(`${path.slice(1)} remote at ${path}`)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: link })).toBeInTheDocument();
  });

  it.each([
    ['trading'],
    ['operations'],
    [],
  ] as const)('opens the reference data for every role: %s', async (...roles) => {
    renderAt('/refdata/calendar', fakeSession(...roles));

    expect(await screen.findByText('refdata remote at /refdata')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Справочник' })).toBeInTheDocument();
  });

  it.each([['trading'], ['operations'], []] as const)(
    'opens prices for every role: %s',
    async (...roles) => {
      renderAt('/marketdata/instruments/i-1', fakeSession(...roles));

      expect(await screen.findByText('marketdata remote at /marketdata')).toBeInTheDocument();
      expect(screen.getByRole('link', { name: 'Цены' })).toBeInTheDocument();
    },
  );

  it.each([['trading'], ['operations'], ['admin']] as const)(
    'opens trades for %s',
    async (...roles) => {
      renderAt('/booking/trades/t-1', fakeSession(...roles));

      expect(await screen.findByText('booking remote at /booking')).toBeInTheDocument();
      expect(screen.getByRole('link', { name: 'Сделки' })).toBeInTheDocument();
    },
  );

  it.each([['trading'], ['operations'], ['admin']] as const)(
    'opens the reconciliation for %s',
    async (...roles) => {
      renderAt('/reconciliation/statements', fakeSession(...roles));

      expect(await screen.findByText('reconciliation remote at /reconciliation')).toBeInTheDocument();
      expect(screen.getByRole('link', { name: 'Сверка' })).toBeInTheDocument();
    },
  );

  it('hides the reconciliation from a user without a role', () => {
    renderAt('/reconciliation', fakeSession());

    expect(screen.queryByRole('link', { name: 'Сверка' })).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('трейдингу и сопровождению');
  });

  it('hides trades from a user without a role', () => {
    renderAt('/booking', fakeSession());

    expect(screen.queryByRole('link', { name: 'Сделки' })).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('трейдингу и сопровождению');
  });

  it.each(['/admin', '/audit'])('hides %s from everyone else', (path) => {
    renderAt(path, fakeSession('trading'));

    expect(screen.queryByRole('link', { name: 'Администрирование' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Журнал' })).not.toBeInTheDocument();
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
