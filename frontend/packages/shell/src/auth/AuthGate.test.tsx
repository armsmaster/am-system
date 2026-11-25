import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { fakeApi, SESSION } from '../test/identity';
import { AuthGate, inviteToken } from './AuthGate';
import { AuthError, type IdentityApi } from './identity';

function place(pathname = '/booking') {
  return { pathname, reload: vi.fn(), assign: vi.fn() };
}

function renderGate(api: IdentityApi, location = place(), history = { replaceState: vi.fn() }) {
  render(
    <AuthGate api={api} location={location} history={history}>
      {(session) => <p>Приложение: {session.displayName}</p>}
    </AuthGate>,
  );
  return history;
}

const NO_SESSION = new AuthError(401, 'no_session', 'Войдите в систему.');

describe('inviteToken', () => {
  it('finds the secret of an invitation link', () => {
    expect(inviteToken('/invite/abc')).toBe('abc');
    expect(inviteToken('/invite/a%2Bb/')).toBe('a+b');
    expect(inviteToken('/invite/')).toBeNull();
    expect(inviteToken('/booking')).toBeNull();
  });
});

describe('AuthGate', () => {
  it('opens the application by the saved session', async () => {
    renderGate(fakeApi());

    expect(screen.getByText('Проверяем вход…')).toBeInTheDocument();
    expect(await screen.findByText('Приложение: Иван Иванов')).toBeInTheDocument();
  });

  it('shows the login page without a session and the application after login', async () => {
    const user = userEvent.setup();
    const api = fakeApi({ refresh: vi.fn(() => Promise.reject(NO_SESSION)) });
    renderGate(api);

    await user.type(await screen.findByLabelText('Логин'), 'ivanov');
    await user.type(screen.getByLabelText('Пароль'), 'secret');
    await user.click(screen.getByRole('button', { name: 'Войти' }));

    expect(await screen.findByText('Приложение: Иван Иванов')).toBeInTheDocument();
  });

  it('offers to retry when the service is unavailable', async () => {
    const user = userEvent.setup();
    const refresh = vi
      .fn()
      .mockRejectedValueOnce(new AuthError(503, 'keycloak_unavailable', 'Keycloak недоступен.'))
      .mockResolvedValueOnce(SESSION);
    renderGate(fakeApi({ refresh }));

    expect(await screen.findByRole('heading', { name: 'Сервис входа недоступен' })).toBeInTheDocument();
    expect(screen.getByText('Keycloak недоступен.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Повторить' }));

    expect(await screen.findByText('Приложение: Иван Иванов')).toBeInTheDocument();
  });

  it('sets the password by an invitation link and leaves the secret out of the address', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    const history = renderGate(api, place('/invite/secret'));

    await user.type(await screen.findByLabelText('Новый пароль'), 'ivan-secret-1');
    await user.type(screen.getByLabelText('Повторите пароль'), 'ivan-secret-1');
    await user.click(screen.getByRole('button', { name: 'Задать пароль и войти' }));

    expect(await screen.findByText('Приложение: Иван Иванов')).toBeInTheDocument();
    expect(history.replaceState).toHaveBeenCalledWith(null, '', '/');
    expect(api.refresh).not.toHaveBeenCalled();
  });

  it('leads from a broken invitation link to the login page', async () => {
    const user = userEvent.setup();
    const invite = vi.fn(() => Promise.reject(new AuthError(404, 'invite_not_found', 'Ссылка не найдена.')));
    const api = fakeApi({ invite, refresh: vi.fn(() => Promise.reject(NO_SESSION)) });
    const history = renderGate(api, place('/invite/wrong'));

    await user.click(await screen.findByRole('button', { name: 'На страницу входа' }));

    expect(await screen.findByRole('heading', { name: 'Вход' })).toBeInTheDocument();
    expect(history.replaceState).toHaveBeenCalledWith(null, '', '/');
  });

  it('opens the application from a used link when already logged in', async () => {
    const user = userEvent.setup();
    const invite = vi.fn(() => Promise.reject(new AuthError(410, 'invite_used', 'Пароль уже задан.')));
    renderGate(fakeApi({ invite }), place('/invite/used'));

    await user.click(await screen.findByRole('button', { name: 'На страницу входа' }));

    expect(await screen.findByText('Приложение: Иван Иванов')).toBeInTheDocument();
  });
});
