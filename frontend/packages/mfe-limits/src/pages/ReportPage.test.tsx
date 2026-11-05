import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { fakeApi, fundDay, renderLimits, report } from '../test/fixtures';

const REPORT = '/limits/report';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ReportPage', () => {
  it('shows the usage at the month end and the breaches of the month', async () => {
    const api = renderLimits(fakeApi(), REPORT);

    expect(await screen.findByText(/DOHOD2, ноябрь 2026: использование на 16\.11\.2026\. Нарушения/)).toBeInTheDocument();
    expect(api.report).toHaveBeenCalledWith('f-dohod', '2026-11');
    const usage = screen.getByRole('table', { name: 'Использование на конец месяца' });
    expect(within(usage).getAllByRole('row')).toHaveLength(4);
    expect(within(usage).getAllByRole('row')[2]).toHaveTextContent(
      'Л3 · Корпоративные облигации AAA и AA+ · на эмитента9,64%, Эмитент Ане более 10%96%близко к лимиту',
    );
    const breaches = screen.getByRole('table', { name: 'Журнал нарушений' });
    expect(within(breaches).getAllByRole('row')[1]).toHaveTextContent('Эмитент Г');
    // В отчёте журнал только для чтения.
    expect(within(breaches).queryByRole('button')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Месяц'), { target: { value: '2026-10' } });
    expect(api.report).toHaveBeenLastCalledWith('f-dohod', '2026-10');
    fireEvent.change(screen.getByLabelText('Месяц'), { target: { value: '' } });
    expect(api.report).toHaveBeenLastCalledWith('f-dohod', '2026-11');
    await userEvent.selectOptions(screen.getByLabelText('Фонд'), 'f-opif');
    expect(api.report).toHaveBeenLastCalledWith('f-opif', '2026-11');
  });

  it('says when there was no set, no calculation, no breach', async () => {
    const api = fakeApi();
    api.report.mockResolvedValueOnce(
      report({ day: fundDay({ date: '2026-10-31', limit_set: null, calculated: false, checks: [] }), breaches: [] }),
    );
    renderLimits(api, REPORT);
    expect(await screen.findByText(/использование на 31\.10\.2026 — набора лимитов на этот день нет/)).toBeInTheDocument();
    expect(screen.getByText('Нарушений за месяц не было.')).toBeInTheDocument();
    expect(screen.queryByRole('table', { name: 'Использование на конец месяца' })).not.toBeInTheDocument();

    api.report.mockResolvedValueOnce(report({ day: fundDay({ calculated: false, totals: null, checks: [] }) }));
    fireEvent.change(screen.getByLabelText('Месяц'), { target: { value: '2026-10' } });
    expect(await screen.findByText(/— расчёта фонда за этот день нет/)).toBeInTheDocument();
  });

  it('downloads the report for Excel', async () => {
    const create = vi.fn((_blob: Blob) => 'blob:report');
    const revoke = vi.fn();
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    renderLimits(fakeApi(), REPORT);

    await userEvent.click(await screen.findByRole('button', { name: 'Скачать для Excel' }));

    const link = click.mock.contexts[0] as HTMLAnchorElement;
    expect(link.href).toBe('blob:report');
    expect(link.download).toBe('limits-DOHOD2-2026-11.csv');
    expect(await create.mock.calls[0]![0].text()).toContain('Использование лимитов: DOHOD2');
    expect(revoke).toHaveBeenCalledWith('blob:report');
  });
});
