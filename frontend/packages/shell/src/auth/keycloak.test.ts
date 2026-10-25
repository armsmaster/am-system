import { beforeEach, describe, expect, it, vi } from 'vitest';

const keycloakMock = vi.hoisted(() => ({
  constructed: [] as unknown[],
  init: vi.fn(() => Promise.resolve(true)),
}));

vi.mock('keycloak-js', () => ({
  default: class {
    token = 'access-token';
    tokenParsed = { preferred_username: 'ivanov', realm_access: { roles: ['admin'] } };
    init = keycloakMock.init;
    constructor(config: unknown) {
      keycloakMock.constructed.push(config);
    }
  },
}));

const { authConfig, startSession } = await import('./keycloak');

describe('authConfig', () => {
  it('uses Keycloak on the same origin by default', () => {
    expect(authConfig(undefined, 'https://am.example')).toEqual({
      url: 'https://am.example/auth',
      realm: 'am',
      clientId: 'am-web',
    });
  });

  it('can point to a separate Keycloak for local development', () => {
    expect(authConfig('http://localhost:8080/auth', 'http://localhost:3000').url).toBe(
      'http://localhost:8080/auth',
    );
  });

  it('reads the environment and window location when called without arguments', () => {
    expect(authConfig().url).toBe(`${window.location.origin}/auth`);
  });
});

describe('startSession', () => {
  beforeEach(() => {
    keycloakMock.constructed.length = 0;
    keycloakMock.init.mockClear();
  });

  it('logs in with PKCE and returns the session', async () => {
    const config = { url: 'https://am.example/auth', realm: 'am', clientId: 'am-web' };

    const session = await startSession(config);

    expect(keycloakMock.constructed).toEqual([config]);
    expect(keycloakMock.init).toHaveBeenCalledWith({
      onLoad: 'login-required',
      pkceMethod: 'S256',
      checkLoginIframe: false,
    });
    expect(session.username).toBe('ivanov');
    expect([...session.roles]).toEqual(['admin']);
  });

  it('uses the default configuration', async () => {
    await startSession();

    expect(keycloakMock.constructed).toEqual([authConfig()]);
  });
});
