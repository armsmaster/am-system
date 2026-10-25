import { parseRoles, type Session } from '@am/platform';
import type Keycloak from 'keycloak-js';

/** Часть keycloak-js, которой пользуется shell. */
export type KeycloakClient = Pick<
  Keycloak,
  'token' | 'tokenParsed' | 'updateToken' | 'login' | 'logout'
>;

/** Токен обновляется, если до его истечения осталось меньше этого числа секунд. */
const MIN_TOKEN_VALIDITY_SECONDS = 30;

export function sessionFromKeycloak(keycloak: KeycloakClient): Session {
  const claims = keycloak.tokenParsed ?? {};
  const username = stringClaim(claims.preferred_username) ?? stringClaim(claims.sub) ?? '';
  const fullName = [stringClaim(claims.given_name), stringClaim(claims.family_name)]
    .filter(Boolean)
    .join(' ');

  return {
    username,
    displayName: fullName || username,
    roles: parseRoles(claims.realm_access?.roles),

    async getAccessToken() {
      try {
        await keycloak.updateToken(MIN_TOKEN_VALIDITY_SECONDS);
      } catch (error) {
        // Сессия в Keycloak закончилась — отправляем на повторный вход.
        await keycloak.login();
        throw error;
      }
      if (!keycloak.token) {
        throw new Error('Keycloak returned no access token');
      }
      return keycloak.token;
    },

    logout() {
      void keycloak.logout({ redirectUri: window.location.origin });
    },
  };
}

function stringClaim(value: unknown): string | undefined {
  return typeof value === 'string' && value !== '' ? value : undefined;
}
