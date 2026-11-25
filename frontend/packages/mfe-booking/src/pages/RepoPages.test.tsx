import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { RepoLine } from '../api';
import { dayResult, fakeApi, ladder, operation, positions, refused, renderBooking, repoTrade } from '../test/fixtures';

/** Сделка Б из методики шага 7 на 01.12: прямое РЕПО, проценты за одну ночь. */
const BORROWED: RepoLine = {
  id: 't-repo',
  portfolio_id: 'p-bonds',
  portfolio_code: 'OPIF1-BONDS',
  direct: true,
  instrument_id: 'i-ofz',
  secid: 'SU26238RMFS4',
  short_name: 'ОФЗ 26238',
  quantity: 10000,
  rate: '14.500000',
  first_date: '2026-11-30',
  second_date: '2026-12-03',
  second_amount: '5616786.01',
  principal: '5610100.00',
  accrued: '2228.67',
  value: '-5612328.67',
};

describe('REPO on the trade pages (design/07)', () => {
  it('lists a REPO with both legs and notes the saved one', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.trades.mockResolvedValue([
      repoTrade(),
      repoTrade({ id: 't-ksu', secid: 'КСУ', side: 'buy', quantity: 0, price: '0', accrued: null }),
    ]);
    api.enter.mockResolvedValue(repoTrade({ quantity: 0, secid: 'КСУ' }));
    renderBooking(api, '/booking?portfolio=p-bonds&date=2026-11-30');

    const [, row, ksu] = within(await screen.findByRole('table', { name: 'Сделки' })).getAllByRole('row');
    expect(row).toHaveTextContent('прямое РЕПО14,5% · до 03.12.202610 000 шт.52,6%');
    expect(ksu).toHaveTextContent('обратное РЕПО14,5% · до 03.12.2026—5 610 100,00');
    expect(row).toHaveTextContent('30.11.2026→ 03.12.2026');

    await user.click(screen.getByRole('button', { name: 'Новая сделка' }));
    const form = within(screen.getByRole('form', { name: 'Новая сделка' }));
    await user.selectOptions(form.getByLabelText('Бумага'), 'i-ofz');
    await user.type(form.getByLabelText('Количество, шт.'), '1');
    await user.type(form.getByLabelText('Цена, % от номинала'), '52');
    await user.click(form.getByRole('button', { name: 'Ввести сделку' }));
    expect(await screen.findByText(/Сделка введена/)).toHaveTextContent(
      'Сделка введена: КСУ, прямое РЕПО, сумма 5 610 100,00 ₽, расчёты 30.11.2026, вторая часть 03.12.2026.',
    );
  });

  it('shows the REPO card and takes a money contribution from operations', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.trade.mockResolvedValue(repoTrade());
    renderBooking(api, '/booking/trades/t-repo', true);

    const panel = await screen.findByRole('dialog', { name: 'Сделка' });
    expect(await within(panel).findByText('прямое РЕПО')).toHaveClass('pill', 'side-repo');
    const facts = document.querySelector<HTMLElement>('dl.facts')!;
    expect(facts).toHaveTextContent('Сумма первой части5 610 100,00 ₽Ставка РЕПО14,5% годовых');
    expect(facts).toHaveTextContent('Вторая часть03.12.2026, 5 616 786,01 ₽');
    expect(facts).toHaveTextContent('Первая часть30.11.2026');

    const form = within(screen.getByRole('form', { name: 'Компенсационный взнос' }));
    fireEvent.change(form.getByLabelText('Дата взноса'), { target: { value: '2026-12-02' } });
    await user.type(form.getByLabelText('Сумма, ₽'), '20 000');
    await user.click(form.getByRole('button', { name: 'Ввести взнос' }));
    expect(await form.findByRole('status')).toHaveTextContent('Взнос введён');
    expect(api.enterOperation).toHaveBeenCalledWith({
      kind: 'repo_contribution',
      trade_id: 't-repo',
      op_date: '2026-12-02',
      direction: 'out',
      amount: '20000',
      quantity: null,
      comment: null,
    });
  });

  it('takes a contribution in bonds and shows a refusal', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.trade.mockResolvedValue(repoTrade());
    api.enterOperation.mockRejectedValue(refused('Взнос — между частями РЕПО.'));
    renderBooking(api, '/booking/trades/t-repo', true);

    const form = within(await screen.findByRole('form', { name: 'Компенсационный взнос' }));
    await user.selectOptions(form.getByLabelText('Кто платит'), 'in');
    await user.selectOptions(form.getByLabelText('Чем'), 'bonds');
    await user.type(form.getByLabelText('Количество, шт.'), '1000');
    await user.click(form.getByRole('button', { name: 'Ввести взнос' }));
    expect(await form.findByRole('alert')).toHaveTextContent('Взнос — между частями РЕПО.');
    expect(api.enterOperation).toHaveBeenCalledWith(
      expect.objectContaining({ direction: 'in', amount: null, quantity: 1000 }),
    );
  });

  it('shows a КСУ REPO without bond fields and offers money only; trading sees no contribution form', async () => {
    const api = fakeApi();
    api.trade.mockResolvedValue(
      repoTrade({ instrument_id: null, instrument_kind: 'ksu', secid: 'КСУ', short_name: 'КСУ', quantity: 0 }),
    );
    renderBooking(api, '/booking/trades/t-repo', true);
    const form = within(await screen.findByRole('form', { name: 'Компенсационный взнос' }));
    expect(within(form.getByLabelText('Чем')).getAllByRole('option')).toHaveLength(1);
    expect(document.querySelector('dl.facts')).not.toHaveTextContent('Количество');

    const trading = fakeApi();
    trading.trade.mockResolvedValue(repoTrade());
    renderBooking(trading, '/booking/trades/t-repo');
    await waitFor(() => expect(trading.trade).toHaveBeenCalled());
    expect(screen.getAllByRole('form', { name: 'Компенсационный взнос' })).toHaveLength(1);
  });
});

describe('REPO in positions, ladder, NAV and operations (design/07)', () => {
  it('lists open REPO deals and names the income passed on', async () => {
    const api = fakeApi();
    const base = positions();
    api.positions.mockResolvedValue(
      positions({
        repos: [
          BORROWED,
          { ...BORROWED, id: 't-ksu', direct: false, instrument_id: null, secid: 'КСУ', short_name: 'КСУ', quantity: 0, value: '30023342.47' },
        ],
        result: dayResult({ repo: '-2228.67' }),
        portfolios: [
          {
            ...base.portfolios[0]!,
            movements: [
              { kind: 'coupon', instrument_id: 'i-ofz', trade_id: 't-repo', operation_id: null, quantity: 0, cash: '354000.00' },
            ],
          },
        ],
      }),
    );
    renderBooking(api, '/booking/positions?portfolio=p-bonds&date=2026-12-01');

    const [, borrowed, placed] = within(await screen.findByRole('table', { name: 'РЕПО' })).getAllByRole('row');
    expect(borrowed).toHaveTextContent(
      'прямоепривлекли · OPIF1-BONDSОФЗ 26238SU26238RMFS410 000 шт.14,530.11.2026 → 03.12.2026вторая часть 5 616 786,01 ₽' +
        '5 610 100,002 228,67−5 612 328,67',
    );
    expect(placed).toHaveTextContent(/^обратноеразместили · OPIF1-BONDSКСУ14,5/);
    expect(screen.getByText('проценты по РЕПО')).toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'Движения дня' })).toHaveTextContent('купон: передача дохода по РЕПО');
  });

  it('shows REPO legs in the ladder', async () => {
    const api = fakeApi();
    const base = ladder();
    api.ladder.mockResolvedValue(
      ladder({
        days: [
          {
            ...base.days[0]!,
            movements: [
              { portfolio_code: 'OPIF1-BONDS', repo_term: 3, kind: 'repo_second', instrument_id: 'i-ofz', trade_id: 't-repo', operation_id: null, quantity: 10000, cash: '-5616786.01' },
            ],
          },
        ],
      }),
    );
    renderBooking(api, '/booking/ladder?account=a-broker');
    // Часть РЕПО — строкой по сроку сделки (решение 166).
    const rows = within(await screen.findByRole('table', { name: 'План по расчётам' })).getAllByRole('row');
    expect(rows[1]).toHaveTextContent('РЕПО3 дня · вторая часть5 616 786,01');
    expect(screen.getByText('ОФЗ 26238 +10 000 → 50 000 шт.')).toHaveClass('mv-security');
    // Состав СЧА (РЕПО в нём) раздел «СЧА» больше не показывает (решение 168): его видно в API /nav.
  });

  it('names a contribution in the operations list', async () => {
    const api = fakeApi();
    api.operations.mockResolvedValue([
      operation({
        kind: 'repo_contribution',
        trade_id: 't-repo',
        direction: 'out',
        secid: 'SU26238RMFS4',
        amount: '20000.00',
        units: null,
      }),
      operation({ id: 'o-2', kind: 'repo_contribution', trade_id: 't-repo', direction: 'in', quantity: 5, units: null }),
    ]);
    renderBooking(api, '/booking/operations?portfolio=p-bonds');
    const [, paid, received] = within(await screen.findByRole('table', { name: 'Операции' })).getAllByRole('row');
    expect(paid).toHaveTextContent('компенсационный взнос по РЕПО, фонд — НКЦ, SU26238RMFS4');
    expect(received).toHaveTextContent('компенсационный взнос по РЕПО, НКЦ — фонду');
  });
});
