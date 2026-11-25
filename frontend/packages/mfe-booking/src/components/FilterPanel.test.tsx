import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { STANDARD_FILTER } from '../test/fixtures';
import { FilterPanel, SETTLE_MS } from './FilterPanel';

const OWN = { ...STANDARD_FILTER, half_life: '1.00', trend_memory: '20.0', q_level: '0.2147', q_trend: '0.00254', standard: false };

function sliders() {
  const [smoothing, memory] = screen.getAllByRole('slider') as HTMLInputElement[];
  return { smoothing: smoothing!, memory: memory! };
}

describe('FilterPanel (decision 174)', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('shows the standard settings in days and the filter noises', () => {
    render(<FilterPanel filter={STANDARD_FILTER} onChange={vi.fn()} />);

    expect(screen.getByText(/скачок наполовину забывается за/)).toHaveTextContent('2,3 торг. дн.');
    expect(screen.getByText(/Память тренда/)).toHaveTextContent('52 торг. дн.');
    expect(screen.getByText(/Шумы фильтра/)).toHaveTextContent('уровня 0,0500 · тренда 0,000500');
    expect(screen.getByRole('button', { name: /По умолчанию: 2,25 и 52,2 дн\./ })).toBeDisabled();
  });

  it('sends new settings once the slider stops', () => {
    const onChange = vi.fn();
    render(<FilterPanel filter={STANDARD_FILTER} onChange={onChange} />);

    fireEvent.change(sliders().smoothing, { target: { value: '0' } });
    fireEvent.change(sliders().memory, { target: { value: '500' } });
    act(() => vi.advanceTimersByTime(SETTLE_MS - 1));
    expect(onChange).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1));

    expect(onChange).toHaveBeenCalledExactlyOnceWith({ halfLife: 0.5, memory: 50 });
    expect(screen.getByText(/скачок наполовину забывается за/)).toHaveTextContent('0,5 торг. дн.');
  });

  it('keeps smoothing possible for a short memory', () => {
    const onChange = vi.fn();
    render(<FilterPanel filter={STANDARD_FILTER} onChange={onChange} />);

    fireEvent.change(sliders().smoothing, { target: { value: '1000' } });
    expect(screen.getByText(/скачок наполовину забывается за/)).toHaveTextContent('3,3 торг. дн.');
    fireEvent.change(sliders().memory, { target: { value: '0' } });
    act(() => vi.advanceTimersByTime(SETTLE_MS));

    expect(onChange).toHaveBeenLastCalledWith({ halfLife: 0.8, memory: 5 });
    expect(screen.getByText(/При памяти тренда 5 дн\. — не больше 0,8 дн\./)).toBeInTheDocument();
  });

  it('goes back to the standard settings', () => {
    const onChange = vi.fn();
    render(<FilterPanel filter={OWN} onChange={onChange} />);
    expect(screen.getByText(/скачок наполовину забывается за/)).toHaveTextContent('1,0 торг. дн.');

    fireEvent.click(screen.getByRole('button', { name: /По умолчанию/ }));
    act(() => vi.advanceTimersByTime(SETTLE_MS));

    expect(onChange).toHaveBeenCalledExactlyOnceWith(null);
    expect(screen.getByText(/Память тренда/)).toHaveTextContent('52 торг. дн.');
  });

  it('writes long smoothing without decimals', () => {
    render(<FilterPanel filter={{ ...OWN, half_life: '10.70', trend_memory: '500.0' }} onChange={vi.fn()} />);

    expect(screen.getByText(/скачок наполовину забывается за/)).toHaveTextContent('11 торг. дн.');
  });
});
