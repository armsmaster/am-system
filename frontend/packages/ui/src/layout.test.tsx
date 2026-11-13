import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { Drawer, FilterChips, PageHeader, Tabs } from './layout';

describe('PageHeader', () => {
  it('shows the title and the actions', () => {
    render(<PageHeader title="Справочник" actions={<button type="button">Загрузить</button>} />);

    expect(screen.getByRole('heading', { name: 'Справочник' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Загрузить' })).toBeInTheDocument();
  });

  it('works without actions', () => {
    const { container } = render(<PageHeader title="Эмитенты" />);

    expect(container.querySelector('.page-actions')).toBeNull();
  });
});

describe('FilterChips', () => {
  it('presses one choice and reports another', async () => {
    const onChange = vi.fn();
    render(
      <FilterChips
        label="Тип"
        choices={[
          { value: 'all', label: 'Все', count: 3 },
          { value: 'bond', label: 'Облигации' },
        ]}
        value="all"
        onChange={onChange}
      />,
    );

    expect(screen.getByRole('button', { name: 'Все 3' })).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(screen.getByRole('button', { name: 'Облигации' }));
    expect(onChange).toHaveBeenCalledWith('bond');
  });
});

describe('Tabs', () => {
  it('selects one tab and reports another', async () => {
    const onChange = vi.fn();
    render(
      <Tabs
        label="Карточка"
        tabs={[
          { value: 'coupons', label: 'Купоны', count: 30 },
          { value: 'versions', label: 'Версии' },
        ]}
        value="coupons"
        onChange={onChange}
      />,
    );

    expect(screen.getByRole('tab', { name: 'Купоны 30' })).toHaveAttribute('aria-selected', 'true');
    await userEvent.click(screen.getByRole('tab', { name: 'Версии' }));
    expect(onChange).toHaveBeenCalledWith('versions');
  });
});

describe('Drawer', () => {
  it('shows nothing when closed', () => {
    const { container } = render(
      <Drawer title="Правка" open={false} onClose={vi.fn()}>
        <p>Форма</p>
      </Drawer>,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it('closes on the button, on Escape and on a click outside', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const { container } = render(
      <Drawer title="Правка" open onClose={onClose}>
        <p>Форма</p>
      </Drawer>,
    );

    expect(screen.getByRole('dialog', { name: 'Правка' })).toHaveTextContent('Форма');
    await user.click(screen.getByRole('button', { name: 'Закрыть' }));
    await user.keyboard('a');
    await user.keyboard('{Escape}');
    await user.click(container.querySelector('.drawer-scrim')!);
    expect(onClose).toHaveBeenCalledTimes(3);
  });
});
