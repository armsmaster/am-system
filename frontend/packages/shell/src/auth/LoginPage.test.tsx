import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { fakeApi, SESSION } from '../test/identity';
import { AuthError } from './identity';
import { LoginPage } from './LoginPage';

const TEMPORARY = new AuthError(409, 'password_change_required', 'Задайте свой пароль вместо временного.');

async function fillLogin(user: ReturnType<typeof userEvent.setup>, password = 'temporary-1') {
  await user.type(screen.getByLabelText('Логин'), '  ivanov ');
  await user.type(screen.getByLabelText('Пароль'), password);
}

describe('LoginPage', () => {
  it('logs in and remembers the user when asked', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    const onSession = vi.fn();
    render(<LoginPage api={api} onSession={onSession} />);
    const submit = screen.getByRole('button', { name: 'Войти' });
    expect(submit).toBeDisabled();

    await fillLogin(user, 'secret');
    await user.click(screen.getByLabelText('Запомнить меня'));
    await user.click(submit);

    expect(api.login).toHaveBeenCalledWith('ivanov', 'secret', true);
    expect(onSession).toHaveBeenCalledWith(SESSION);
  });

  it('explains a refusal', async () => {
    const user = userEvent.setup();
    const refusal = new AuthError(401, 'invalid_credentials', 'Неверный логин или пароль.');
    const login = vi.fn().mockRejectedValueOnce(refusal).mockRejectedValueOnce(new Error('boom'));
    render(<LoginPage api={fakeApi({ login })} onSession={vi.fn()} />);

    await fillLogin(user);
    await user.click(screen.getByRole('button', { name: 'Войти' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Неверный логин или пароль.');

    await user.click(screen.getByRole('button', { name: 'Войти' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Что-то пошло не так');
  });

  it('tells what to do with a forgotten password', async () => {
    const user = userEvent.setup();
    render(<LoginPage api={fakeApi()} onSession={vi.fn()} />);
    const forgot = screen.getByRole('button', { name: 'Забыли пароль?' });

    await user.click(forgot);
    expect(screen.getByText(/Попросите администратора выдать новую ссылку/)).toBeInTheDocument();
    await user.click(forgot);
    expect(screen.queryByText(/Попросите администратора/)).not.toBeInTheDocument();
  });

  it('replaces a temporary password before the first login', async () => {
    const user = userEvent.setup();
    const api = fakeApi({ login: vi.fn(() => Promise.reject(TEMPORARY)) });
    const onSession = vi.fn();
    render(<LoginPage api={api} onSession={onSession} />);
    await fillLogin(user);
    await user.click(screen.getByRole('button', { name: 'Войти' }));

    expect(await screen.findByRole('heading', { name: 'Задайте свой пароль' })).toBeInTheDocument();
    const save = screen.getByRole('button', { name: 'Сохранить и войти' });
    await user.type(screen.getByLabelText('Новый пароль'), 'Ivanov');
    expect(screen.getByText('Не совпадает с логином').closest('li')).not.toHaveClass('ok');
    await user.type(screen.getByLabelText('Новый пароль'), '-secret-1');
    await user.type(screen.getByLabelText('Повторите пароль'), 'Ivanov-secret-1');
    expect(screen.getByText('Пароли совпадают').closest('li')).toHaveClass('ok');
    await user.click(save);

    expect(api.firstLogin).toHaveBeenCalledWith('ivanov', 'temporary-1', 'Ivanov-secret-1', false);
    expect(onSession).toHaveBeenCalledWith(SESSION);
  });

  it('shows a refusal of the new password and goes back to the login', async () => {
    const user = userEvent.setup();
    const firstLogin = vi.fn(() => Promise.reject(new AuthError(422, 'weak_password', 'Пароль не подходит.')));
    render(<LoginPage api={fakeApi({ login: vi.fn(() => Promise.reject(TEMPORARY)), firstLogin })} onSession={vi.fn()} />);
    await fillLogin(user);
    await user.click(screen.getByRole('button', { name: 'Войти' }));
    await user.type(await screen.findByLabelText('Новый пароль'), 'long-enough-1');
    await user.type(screen.getByLabelText('Повторите пароль'), 'long-enough-1');

    await user.click(screen.getByRole('button', { name: 'Сохранить и войти' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Пароль не подходит.');

    await user.click(screen.getByRole('button', { name: 'Назад ко входу' }));
    expect(screen.getByLabelText('Логин')).toHaveValue('  ivanov ');
    expect(screen.getByLabelText('Пароль')).toHaveValue('');
  });
});
