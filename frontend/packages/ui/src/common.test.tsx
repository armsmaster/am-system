import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ErrorNote, LoadedView } from './common';

describe('ErrorNote', () => {
  it('shows nothing without a message', () => {
    const { container } = render(<ErrorNote message={null} />);

    expect(container).toBeEmptyDOMElement();
  });

  it('shows the message as an alert', () => {
    render(<ErrorNote message="Сервер отказал" />);

    expect(screen.getByRole('alert')).toHaveTextContent('Сервер отказал');
  });
});

describe('LoadedView', () => {
  const view = (data: string) => <p>Данные: {data}</p>;

  it('shows loading', () => {
    render(<LoadedView state={{ status: 'loading' }}>{view}</LoadedView>);

    expect(screen.getByText('Загрузка…')).toBeInTheDocument();
  });

  it('shows the failure', () => {
    render(<LoadedView state={{ status: 'failed', message: 'Нет связи' }}>{view}</LoadedView>);

    expect(screen.getByRole('alert')).toHaveTextContent('Нет связи');
  });

  it('passes ready data to children', () => {
    render(<LoadedView state={{ status: 'ready', data: 'есть' }}>{view}</LoadedView>);

    expect(screen.getByText('Данные: есть')).toBeInTheDocument();
  });
});
