import type { Role, Session } from '@am/platform';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import AdminApp from './App';

function session(...roles: Role[]): Session {
  return {
    username: 'u',
    displayName: 'U',
    roles: new Set(roles),
    getAccessToken: () => Promise.resolve('t'),
    logout: () => undefined,
  };
}

describe('AdminApp', () => {
  it('shows the admin section to an admin', () => {
    render(<AdminApp session={session('admin')} basePath="/admin" />);

    expect(screen.getByRole('heading', { name: 'Администрирование' })).toBeInTheDocument();
  });

  it('refuses everyone else', () => {
    render(<AdminApp session={session('trading', 'operations')} basePath="/admin" />);

    expect(screen.getByRole('alert')).toHaveTextContent('только администратору');
  });
});
