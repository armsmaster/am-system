import { describe, expect, it, vi } from 'vitest';

import { sessionFromKeycloak, type KeycloakClient } from './session';

// В тестах нужно уметь «стереть» токен, поэтому undefined разрешён явно.
type Overrides = { [K in keyof KeycloakClient]?: KeycloakClient[K] | undefined };

function keycloak(overrides: Overrides = {}): KeycloakClient {
  return {
    token: 'access-token',
    tokenParsed: {
      sub: 'u-1',
      preferred_username: 'ivanov',
      given_name: 'Иван',
      family_name: 'Иванов',
      realm_access: { roles: ['trading', 'offline_access'] },
    },
    updateToken: vi.fn(() => Promise.resolve(false)),
    login: vi.fn(() => Promise.resolve()),
    logout: vi.fn(() => Promise.resolve()),
    ...overrides,
  } as KeycloakClient;
}

describe('sessionFromKeycloak', () => {
  it('takes user and system roles from the token', () => {
    const session = sessionFromKeycloak(keycloak());

    expect(session.username).toBe('ivanov');
    expect(session.displayName).toBe('Иван Иванов');
    expect([...session.roles]).toEqual(['trading']);
  });

  it('falls back to username and subject when names are missing', () => {
    expect(sessionFromKeycloak(keycloak({ tokenParsed: { sub: 'u-1' } }))).toMatchObject({
      username: 'u-1',
      displayName: 'u-1',
    });
    expect(sessionFromKeycloak(keycloak({ tokenParsed: undefined }))).toMatchObject({
      username: '',
      displayName: '',
    });
  });

  it('refreshes the token before handing it out', async () => {
    const client = keycloak();

    expect(await sessionFromKeycloak(client).getAccessToken()).toBe('access-token');
    expect(client.updateToken).toHaveBeenCalledWith(30);
  });

  it('sends the user to login when the Keycloak session is over', async () => {
    const client = keycloak({ updateToken: vi.fn(() => Promise.reject(new Error('expired'))) });

    await expect(sessionFromKeycloak(client).getAccessToken()).rejects.toThrow('expired');
    expect(client.login).toHaveBeenCalledOnce();
  });

  it('fails loudly if Keycloak has no token', async () => {
    const client = keycloak({ token: undefined });

    await expect(sessionFromKeycloak(client).getAccessToken()).rejects.toThrow('no access token');
  });

  it('logs out back to the application', () => {
    const client = keycloak();

    sessionFromKeycloak(client).logout();

    expect(client.logout).toHaveBeenCalledWith({ redirectUri: window.location.origin });
  });
});
