import { ApiError } from '@am/platform';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { IdentityApi, Person } from '../api';
import { UsersPage } from './UsersPage';

function person(username: string, fields: Partial<Person> = {}): Person {
  return {
    id: `u-${username}`,
    username,
    first_name: '',
    last_name: '',
    full_name: '',
    email: null,
    enabled: true,
    roles: [],
    status: 'active',
    invite_expires_at: null,
    last_login_at: null,
    ...fields,
  };
}

const PEOPLE: readonly Person[] = [
  person('admin', { full_name: 'Анна Админова', roles: ['admin'], last_login_at: '2026-11-25T10:20:00Z' }),
  person('trader', {
    full_name: 'Тимур Трейдеров',
    email: 'trader@am.local',
    roles: ['trading', 'risk'],
  }),
  person('ivan.petrov', { full_name: 'Иван Петров', status: 'pending', invite_expires_at: '2026-11-28T11:05:00Z' }),
  person('old', { status: 'expired', invite_expires_at: '2026-11-01T11:05:00Z' }),
  person('gone', { full_name: 'Гена Ушедший', enabled: false, status: 'blocked' }),
];

const INVITE = { token: 'secret-token', expires_at: '2026-12-02T11:05:00Z' };

function fakeIdentity(people: readonly Person[] = PEOPLE) {
  return {
    people: vi.fn(() => Promise.resolve(people)),
    create: vi.fn((body: { username: string }) =>
      Promise.resolve({ user: person(body.username, { status: 'pending' }), invite: INVITE }),
    ),
    setRoles: vi.fn(() => Promise.resolve(PEOPLE[1]!)),
    setEnabled: vi.fn(() => Promise.resolve(PEOPLE[1]!)),
    invite: vi.fn(() => Promise.resolve(INVITE)),
  };
}

const asApi = (api: ReturnType<typeof fakeIdentity>) => api as unknown as IdentityApi;

function renderPage(api = fakeIdentity()) {
  render(<UsersPage api={asApi(api)} self="admin" />);
  return api;
}

async function openMenu(username: string) {
  await userEvent.click(await screen.findByRole('button', { name: `Действия: ${username}` }));
  return within(screen.getByRole('menu', { name: `Действия: ${username}` }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('UsersPage', () => {
  it('lists people with roles, status and last login', async () => {
    renderPage();

    const trader = (await screen.findByText('Тимур Трейдеров')).closest('tr')!;
    expect(within(trader).getByRole('list', { name: 'Роли trader' })).toHaveTextContent('ТрейдингРиск-менеджмент');
    expect(within(trader).getByText('trader@am.local')).toBeInTheDocument();
    expect(within(trader).getByText('активен')).toBeInTheDocument();
    const admin = screen.getByText('Анна Админова').closest('tr')!;
    expect(within(admin).getByText(/25\.11\.2026/)).toBeInTheDocument();
    expect(screen.getByText('ждёт первого входа')).toBeInTheDocument();
    expect(screen.getByText(/ссылка до 28\.11\.2026/)).toBeInTheDocument();
    expect(screen.getByText('ссылка истекла')).toBeInTheDocument();
    expect(screen.getByText('выдайте новую')).toBeInTheDocument();
    const old = screen.getAllByText('old')[0]!.closest('tr')!;
    expect(within(old).getByText('нет ролей')).toBeInTheDocument();
    expect(screen.getByText('Гена Ушедший').closest('tr')).toHaveClass('blocked-row');
  });

  it('filters by status', async () => {
    renderPage();
    await screen.findByText('Тимур Трейдеров');
    const filters = within(screen.getByRole('group', { name: 'Статус' }));

    await userEvent.click(filters.getByRole('button', { name: /Ждут входа/ }));
    expect(screen.getByText('Иван Петров')).toBeInTheDocument();
    expect(screen.queryByText('Тимур Трейдеров')).not.toBeInTheDocument();
    await userEvent.click(filters.getByRole('button', { name: /Заблокированные/ }));
    expect(screen.getByText('Гена Ушедший')).toBeInTheDocument();
    await userEvent.click(filters.getByRole('button', { name: /Активные/ }));
    expect(screen.getByText('Тимур Трейдеров')).toBeInTheDocument();
    await userEvent.click(filters.getByRole('button', { name: /Все/ }));
    expect(screen.getByText('Гена Ушедший')).toBeInTheDocument();
  });

  it('says when nobody matches the filter', async () => {
    renderPage(fakeIdentity([PEOPLE[0]!]));
    await screen.findByText('Анна Админова');

    await userEvent.click(screen.getByRole('button', { name: /Заблокированные/ }));

    expect(screen.getByText('Таких пользователей нет.')).toBeInTheDocument();
  });

  it('creates a person and shows the link to copy', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const api = renderPage();
    await screen.findByText('Тимур Трейдеров');
    await user.click(screen.getByRole('button', { name: 'Новый пользователь' }));
    const form = within(screen.getByRole('form', { name: 'Новый пользователь' }));
    const submit = form.getByRole('button', { name: 'Создать и получить ссылку' });
    expect(submit).toBeDisabled();

    await user.type(form.getByLabelText('Логин'), 'Иван');
    expect(form.getByText(/от 2 символов/)).toHaveClass('field-error');
    await user.clear(form.getByLabelText('Логин'));
    await user.type(form.getByLabelText('Логин'), ' Ivan.Petrov ');
    await user.type(form.getByLabelText('Имя'), ' Иван ');
    await user.type(form.getByLabelText('Фамилия'), 'Петров');
    await user.type(form.getByLabelText(/Email/), 'ivan@');
    expect(form.getByText(/Проверьте адрес/)).toBeInTheDocument();
    expect(submit).toBeDisabled();
    await user.clear(form.getByLabelText(/Email/));
    await user.click(form.getByLabelText(/Риск-менеджмент/));
    await user.click(form.getByLabelText(/Трейдинг/));
    await user.click(form.getByLabelText(/Трейдинг/));
    await user.click(form.getByLabelText(/Сопровождение/));
    await user.click(form.getByLabelText('3 дня'));
    await user.click(submit);

    expect(api.create).toHaveBeenCalledWith({
      username: 'ivan.petrov',
      first_name: 'Иван',
      last_name: 'Петров',
      email: null,
      roles: ['operations', 'risk'],
      ttl_days: 3,
    });
    const done = within(await screen.findByRole('dialog', { name: 'Пользователь создан' }));
    expect(done.getByText(/создан\./)).toHaveTextContent('Пользователь ivan.petrov создан. Передайте ему ссылку: по ней');
    expect(done.getByLabelText('Ссылка для входа')).toHaveValue(`${window.location.origin}/invite/secret-token`);
    // Щелчок в поле выделяет ссылку целиком — удобно скопировать вручную.
    await user.click(done.getByLabelText('Ссылка для входа'));
    const field = done.getByLabelText<HTMLInputElement>('Ссылка для входа');
    expect(field.selectionEnd! - field.selectionStart!).toBe(field.value.length);
    await user.click(done.getByRole('button', { name: 'Скопировать' }));
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/invite/secret-token`);
    expect(done.getByRole('button', { name: 'Скопировано' })).toBeInTheDocument();
    expect(api.people).toHaveBeenCalledTimes(2);
    await user.click(done.getByRole('button', { name: 'Готово' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('asks to copy by hand when the browser refuses and shows a refusal of creation', async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn(() => Promise.reject(new Error('denied'))) },
      configurable: true,
    });
    const api = fakeIdentity();
    api.create
      .mockRejectedValueOnce(new ApiError(409, { detail: { code: 'username_taken', message: 'Такой логин уже занят.' } }))
      .mockResolvedValueOnce({ user: person('ivan'), invite: INVITE });
    renderPage(api);
    await screen.findByText('Тимур Трейдеров');
    await user.click(screen.getByRole('button', { name: 'Новый пользователь' }));
    const form = within(screen.getByRole('form', { name: 'Новый пользователь' }));
    await user.type(form.getByLabelText('Логин'), 'ivan');
    await user.type(form.getByLabelText('Имя'), 'Иван');
    await user.type(form.getByLabelText('Фамилия'), 'Петров');
    await user.type(form.getByLabelText(/Email/), 'ivan@am.local');

    await user.click(form.getByRole('button', { name: 'Создать и получить ссылку' }));
    expect(await form.findByRole('alert')).toHaveTextContent('Такой логин уже занят.');
    expect(api.create).toHaveBeenLastCalledWith(expect.objectContaining({ email: 'ivan@am.local', roles: [] }));

    await user.click(form.getByRole('button', { name: 'Создать и получить ссылку' }));
    const done = within(await screen.findByRole('dialog', { name: 'Пользователь создан' }));
    await user.click(done.getByRole('button', { name: 'Скопировать' }));
    expect(await done.findByText(/скопируйте вручную/)).toBeInTheDocument();
  });

  it('changes roles; own admin role stays', async () => {
    const user = userEvent.setup();
    const api = renderPage();

    await user.click((await openMenu('trader')).getByRole('menuitem', { name: 'Изменить роли' }));
    const form = within(screen.getByRole('form', { name: 'Роли' }));
    expect(form.getByLabelText(/Риск-менеджмент/)).toBeChecked();
    await user.click(form.getByLabelText(/Риск-менеджмент/));
    await user.click(form.getByRole('button', { name: 'Сохранить' }));

    expect(api.setRoles).toHaveBeenCalledWith('u-trader', ['trading']);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    await user.click((await openMenu('admin')).getByRole('menuitem', { name: 'Изменить роли' }));
    const own = within(screen.getByRole('form', { name: 'Роли' }));
    expect(own.getByLabelText(/Администратор/)).toBeDisabled();
    expect(own.getByText('Свою роль администратора снять нельзя')).toBeInTheDocument();
  });

  it('issues a new login link', async () => {
    const user = userEvent.setup();
    const api = renderPage();

    await user.click((await openMenu('old')).getByRole('menuitem', { name: 'Новая ссылка для входа' }));
    const form = within(screen.getByRole('form', { name: 'Новая ссылка' }));
    expect(form.getByText(/так восстанавливают забытый/)).toBeInTheDocument();
    await user.click(form.getByLabelText('1 день'));
    await user.click(form.getByRole('button', { name: 'Выдать ссылку' }));

    expect(api.invite).toHaveBeenCalledWith('u-old', 1);
    const done = within(await screen.findByRole('dialog', { name: 'Ссылка для входа' }));
    expect(done.getByText(/Передайте ссылку пользователю/)).toHaveTextContent('Передайте ссылку пользователю old: по ней');
  });

  it('blocks and unblocks, but not oneself', async () => {
    const user = userEvent.setup();
    const api = renderPage();

    const own = await openMenu('admin');
    expect(own.queryByRole('menuitem', { name: 'Заблокировать' })).not.toBeInTheDocument();
    // Другая клавиша меню не закрывает, Escape — закрывает.
    await user.keyboard('a');
    expect(screen.getByRole('menu')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();

    await user.click((await openMenu('trader')).getByRole('menuitem', { name: 'Заблокировать' }));
    const panel = within(screen.getByRole('dialog', { name: 'Заблокировать пользователя' }));
    expect(panel.getByText(/Запомнить меня/)).toBeInTheDocument();
    await user.click(panel.getByRole('button', { name: 'Заблокировать' }));
    expect(api.setEnabled).toHaveBeenCalledWith('u-trader', false);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    const blocked = await openMenu('gone');
    expect(blocked.queryByRole('menuitem', { name: 'Новая ссылка для входа' })).not.toBeInTheDocument();
    await user.click(blocked.getByRole('menuitem', { name: 'Разблокировать' }));
    expect(api.setEnabled).toHaveBeenCalledWith('u-gone', true);
  });

  it('shows a refusal to unblock and closes the menu by a click outside', async () => {
    const user = userEvent.setup();
    const api = fakeIdentity();
    api.setEnabled.mockRejectedValueOnce(new ApiError(503, null));
    renderPage(api);

    await openMenu('gone');
    await user.click(document.querySelector('.menu-scrim')!);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Действия: gone' }));
    await user.click(screen.getByRole('button', { name: 'Действия: gone' }));
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();

    await user.click((await openMenu('gone')).getByRole('menuitem', { name: 'Разблокировать' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Сервер ответил ошибкой 503');
  });
});
