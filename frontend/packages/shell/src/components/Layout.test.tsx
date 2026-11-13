import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { COLLAPSED_KEY, THEME_KEY } from '../preferences';
import { accessOf } from '../sections';
import { fakeSession } from '../test/session';
import { initials, Layout } from './Layout';

function renderLayout(path = '/', session = fakeSession('admin', 'trading')) {
  const view = render(
    <MemoryRouter initialEntries={[path]}>
      <Layout session={session} access={accessOf(session)}>
        <p>Содержимое</p>
      </Layout>
    </MemoryRouter>,
  );
  return { ...view, layout: view.container.querySelector('.layout')! };
}

beforeEach(() => {
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('Layout', () => {
  it('groups the sections the user can open', () => {
    renderLayout('/', fakeSession());

    const nav = screen.getByRole('navigation', { name: 'Разделы' });
    expect(within(nav).getAllByRole('link').map((l) => l.textContent)).toEqual(['Главная', 'Справочник', 'Цены']);
    expect(within(nav).getByRole('group', { name: 'Рынок' })).toBeInTheDocument();
    // Пустые группы не показываются.
    expect(within(nav).queryByRole('group', { name: 'Портфели' })).not.toBeInTheDocument();
    expect(within(nav).queryByRole('group', { name: 'Управление' })).not.toBeInTheDocument();
  });

  it.each([
    ['/', 'Главная'],
    ['/booking/trades/t-1', 'Портфели/Сделки'],
    ['/audit', 'Управление/Журнал'],
    ['/nowhere', ''],
  ])('says where the user is: %s', (path, where) => {
    renderLayout(path);

    expect(screen.getByLabelText('Где вы')).toHaveTextContent(where === '' ? /^$/ : where);
  });

  it('collapses the sidebar and remembers it', async () => {
    const { layout, unmount } = renderLayout();

    await userEvent.click(screen.getByRole('button', { name: 'Свернуть панель' }));

    expect(layout).toHaveClass('collapsed');
    expect(window.localStorage.getItem(COLLAPSED_KEY)).toBe('true');
    // Подпись — в подсказке: в полосе иконок её не видно.
    expect(screen.getByRole('link', { name: 'Сделки' })).toHaveAttribute('title', 'Сделки');
    unmount();

    const again = renderLayout();
    expect(again.layout).toHaveClass('collapsed');
    await userEvent.click(screen.getByRole('button', { name: 'Развернуть панель' }));
    expect(again.layout).not.toHaveClass('collapsed');
    expect(screen.getByRole('link', { name: 'Сделки' })).not.toHaveAttribute('title');
  });

  it('switches the theme and remembers it', async () => {
    renderLayout();
    expect(screen.getByRole('button', { name: 'Тема как в системе' })).toHaveAttribute('aria-pressed', 'true');

    await userEvent.click(screen.getByRole('button', { name: 'Тёмная тема' }));
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(window.localStorage.getItem(THEME_KEY)).toBe('dark');
    expect(screen.getByRole('button', { name: 'Тёмная тема' })).toHaveAttribute('aria-pressed', 'true');

    await userEvent.click(screen.getByRole('button', { name: 'Тема как в системе' }));
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });

  it.each([
    ['light', 'light'],
    ['purple', undefined],
  ])('starts from the stored theme: %s', (stored, applied) => {
    window.localStorage.setItem(THEME_KEY, stored);
    renderLayout();

    expect(document.documentElement.dataset.theme).toBe(applied);
  });

  it('works when the browser storage is unavailable', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied');
    });
    const { layout } = renderLayout();

    await userEvent.click(screen.getByRole('button', { name: 'Свернуть панель' }));
    await userEvent.click(screen.getByRole('button', { name: 'Светлая тема' }));

    expect(layout).toHaveClass('collapsed');
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('opens the user menu and closes it on Escape or a click outside', async () => {
    const user = userEvent.setup();
    renderLayout('/', fakeSession());
    const toggle = screen.getByRole('button', { name: 'Иван Иванов' });

    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('dialog', { name: 'Пользователь' })).toHaveTextContent('Иван ИвановРолей нет');
    // Щелчок внутри меню его не закрывает.
    fireEvent.mouseDown(screen.getByRole('dialog', { name: 'Пользователь' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    // Другая клавиша — тоже.
    await user.keyboard('a');
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    await user.click(toggle);
    fireEvent.mouseDown(screen.getByText('Содержимое'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    await user.click(toggle);
    await user.click(toggle);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('opens the sections over the page on a narrow screen', async () => {
    const user = userEvent.setup();
    const { layout } = renderLayout();

    await user.click(screen.getByRole('button', { name: 'Открыть разделы' }));
    expect(layout).toHaveClass('drawer-open');
    await user.click(layout.querySelector('.scrim')!);
    expect(layout).not.toHaveClass('drawer-open');

    // Переход в раздел закрывает панель.
    await user.click(screen.getByRole('button', { name: 'Открыть разделы' }));
    await user.click(screen.getByRole('link', { name: 'Цены' }));
    expect(layout).not.toHaveClass('drawer-open');
  });

  it.each([
    ['Иван Иванов', 'ИИ'],
    ['admin', 'AD'],
    ['  ', '?'],
  ])('initials of %j', (name, letters) => {
    expect(initials(name)).toBe(letters);
  });
});
