import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { fakeApi } from '../test/identity';
import { fakeSession } from '../test/session';
import { ChangePassword } from './ChangePassword';
import { AuthError } from './identity';

async function fill(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('Текущий пароль'), 'old-secret');
  await user.type(screen.getByLabelText('Новый пароль'), 'fresh-secret-1');
  await user.type(screen.getByLabelText('Повторите пароль'), 'fresh-secret-1');
}

describe('ChangePassword', () => {
  it('changes the password with the current access token', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    const onClose = vi.fn();
    render(<ChangePassword session={fakeSession()} api={api} onClose={onClose} />);
    expect(screen.getByRole('button', { name: 'Сменить пароль' })).toBeDisabled();

    await fill(user);
    await user.click(screen.getByRole('button', { name: 'Сменить пароль' }));

    expect(api.changePassword).toHaveBeenCalledWith('token', 'old-secret', 'fresh-secret-1');
    expect(await screen.findByRole('status')).toHaveTextContent('Пароль изменён.');
    await user.click(screen.getByRole('button', { name: 'Готово' }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('explains a refusal', async () => {
    const user = userEvent.setup();
    const wrong = new AuthError(422, 'wrong_password', 'Текущий пароль указан неверно.');
    render(
      <ChangePassword
        session={fakeSession()}
        api={fakeApi({ changePassword: vi.fn(() => Promise.reject(wrong)) })}
        onClose={vi.fn()}
      />,
    );

    await fill(user);
    await user.click(screen.getByRole('button', { name: 'Сменить пароль' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Текущий пароль указан неверно.');
  });
});
