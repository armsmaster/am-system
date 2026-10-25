import { describe, expect, it } from 'vitest';

import { hasRole, parseRoles, type Role } from './session';

const withRoles = (...roles: Role[]) => ({ roles: new Set(roles) });

describe('hasRole', () => {
  it('grants the role the user has', () => {
    expect(hasRole(withRoles('trading'), 'trading')).toBe(true);
  });

  it('denies a role the user does not have', () => {
    expect(hasRole(withRoles('trading'), 'operations')).toBe(false);
  });

  it('treats admin as having every role', () => {
    expect(hasRole(withRoles('admin'), 'trading')).toBe(true);
    expect(hasRole(withRoles('admin'), 'operations')).toBe(true);
  });
});

describe('parseRoles', () => {
  it('keeps system roles and drops Keycloak service roles', () => {
    const roles = parseRoles(['offline_access', 'trading', 'uma_authorization', 'admin']);

    expect([...roles].sort()).toEqual(['admin', 'trading']);
  });

  it.each([undefined, null, 'admin', { roles: ['admin'] }])('gives no roles for %j', (raw) => {
    expect(parseRoles(raw).size).toBe(0);
  });
});
