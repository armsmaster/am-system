import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { PORTFOLIOS } from '../test/fixtures';
import { PortfolioTree } from './PortfolioTree';

describe('PortfolioTree', () => {
  it('shows roots with nested children', () => {
    render(<PortfolioTree portfolios={PORTFOLIOS} selectedId={null} onSelect={vi.fn()} />);

    const tree = screen.getByRole('tree', { name: 'Портфели' });
    const roots = within(tree).getAllByRole('treeitem').filter((item) => item.parentElement === tree);
    expect(roots.map((item) => item.querySelector('.code')?.textContent)).toEqual([
      'BOOK',
      'F1',
      'F2',
    ]);
  });

  it('shows a portfolio under each of its parents and marks it', () => {
    render(<PortfolioTree portfolios={PORTFOLIOS} selectedId={null} onSelect={vi.fn()} />);

    expect(screen.getAllByText('F1-OFZ')).toHaveLength(2);
    expect(screen.getAllByText('в 2 местах')).toHaveLength(4);
  });

  it('labels funds, physicals and the treasury', () => {
    render(<PortfolioTree portfolios={PORTFOLIOS} selectedId={null} onSelect={vi.fn()} />);

    expect(screen.getAllByText('фонд')).toHaveLength(2);
    expect(screen.getByText('казначейство')).toBeInTheDocument();
    expect(screen.getAllByText('physical').length).toBeGreaterThan(0);
  });

  it('selects a portfolio on click', async () => {
    const onSelect = vi.fn();
    render(<PortfolioTree portfolios={PORTFOLIOS} selectedId="id-F1" onSelect={onSelect} />);

    await userEvent.click(screen.getAllByRole('button', { name: /F2-MAIN/ })[0]!);

    expect(onSelect).toHaveBeenCalledWith('id-F2-MAIN');
    expect(screen.getByRole('button', { name: /^F1Портфель F1/ })).toHaveClass('selected');
  });

  it('suggests where to start when empty', () => {
    render(<PortfolioTree portfolios={[]} selectedId={null} onSelect={vi.fn()} />);

    expect(screen.getByText(/Начните с фонда/)).toBeInTheDocument();
  });
});
