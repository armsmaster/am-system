import type { MicrofrontendProps } from '@am/platform';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { MicrofrontendModule } from '../microfrontends';
import { fakeSession } from '../test/session';
import { Microfrontend } from './Microfrontend';

const remote: MicrofrontendModule = {
  default: ({ session, basePath }: MicrofrontendProps) => (
    <p>
      {session.username} at {basePath}
    </p>
  ),
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe('Microfrontend', () => {
  it('passes the session and base path to the remote', async () => {
    render(
      <Microfrontend
        load={() => Promise.resolve(remote)}
        session={fakeSession('admin')}
        basePath="/admin"
      />,
    );

    expect(screen.getByText('Загрузка раздела…')).toBeInTheDocument();
    expect(await screen.findByText('ivanov at /admin')).toBeInTheDocument();
  });

  it('keeps the shell alive when the remote fails and lets the user retry', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const load = vi
      .fn<() => Promise<MicrofrontendModule>>()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValue(remote);

    render(<Microfrontend load={load} session={fakeSession()} basePath="/admin" />);

    expect(await screen.findByRole('alert')).toHaveTextContent('временно недоступен');
    await userEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    expect(await screen.findByText('ivanov at /admin')).toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(2);
  });
});
