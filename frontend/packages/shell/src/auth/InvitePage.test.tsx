import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { fakeApi, INVITE, SESSION } from '../test/identity';
import { AuthError, type InviteInfo } from './identity';
import { InvitePage } from './InvitePage';

describe('InvitePage', () => {
  it('sets the password by the link and logs in', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    const onSession = vi.fn();
    render(<InvitePage token="secret" api={api} onSession={onSession} onLogin={vi.fn()} />);
    expect(screen.getByText('Проверяем ссылку…')).toBeInTheDocument();

    expect(await screen.findByText(/Здравствуйте, Иван Петров!/)).toHaveTextContent('Ваш логин — ivan.petrov');
    expect(screen.getByText(/Ссылка действует до 28\.11\.2026/)).toBeInTheDocument();
    const submit = screen.getByRole('button', { name: 'Задать пароль и войти' });
    expect(submit).toBeDisabled();
    await user.type(screen.getByLabelText('Новый пароль'), 'ivan-secret-1');
    await user.type(screen.getByLabelText('Повторите пароль'), 'ivan-secret-1');
    await user.click(screen.getByLabelText('Запомнить меня'));
    await user.click(submit);

    expect(api.invite).toHaveBeenCalledWith('secret');
    expect(api.acceptInvite).toHaveBeenCalledWith('secret', 'ivan-secret-1', true);
    expect(onSession).toHaveBeenCalledWith(SESSION);
  });

  it('greets without a name and explains a refusal', async () => {
    const user = userEvent.setup();
    const acceptInvite = vi.fn(() => Promise.reject(new AuthError(410, 'invite_used', 'Пароль уже задан.')));
    const invite = vi.fn(() => Promise.resolve({ ...INVITE, full_name: '' }));
    render(<InvitePage token="t" api={fakeApi({ invite, acceptInvite })} onSession={vi.fn()} onLogin={vi.fn()} />);

    expect(await screen.findByText(/Здравствуйте!/)).toBeInTheDocument();
    await user.type(screen.getByLabelText('Новый пароль'), 'ivan-secret-1');
    await user.type(screen.getByLabelText('Повторите пароль'), 'ivan-secret-1');
    await user.click(screen.getByRole('button', { name: 'Задать пароль и войти' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Пароль уже задан.');
  });

  it('explains a broken link and leads to the login page', async () => {
    const user = userEvent.setup();
    const expired = new AuthError(410, 'invite_expired', 'Срок ссылки истёк. Попросите у администратора новую.');
    const onLogin = vi.fn();
    render(
      <InvitePage
        token="t"
        api={fakeApi({ invite: vi.fn(() => Promise.reject(expired)) })}
        onSession={vi.fn()}
        onLogin={onLogin}
      />,
    );

    expect(await screen.findByRole('heading', { name: 'Ссылка не работает' })).toBeInTheDocument();
    expect(screen.getByText(/Срок ссылки истёк/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'На страницу входа' }));
    expect(onLogin).toHaveBeenCalledOnce();
  });

  it('ignores an answer that comes after leaving the page', async () => {
    let resolve: (info: InviteInfo) => void = () => undefined;
    let reject: (error: unknown) => void = () => undefined;
    const invite = vi
      .fn()
      .mockReturnValueOnce(new Promise<InviteInfo>((ok) => (resolve = ok)))
      .mockReturnValueOnce(new Promise<InviteInfo>((_ok, fail) => (reject = fail)));
    const api = fakeApi({ invite });

    render(<InvitePage token="a" api={api} onSession={vi.fn()} onLogin={vi.fn()} />).unmount();
    render(<InvitePage token="b" api={api} onSession={vi.fn()} onLogin={vi.fn()} />).unmount();
    resolve(INVITE);
    reject(new Error('late'));
    await Promise.resolve();

    expect(invite).toHaveBeenCalledTimes(2);
  });
});
