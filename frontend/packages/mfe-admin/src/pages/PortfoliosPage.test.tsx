import { ApiError } from '@am/platform';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { asApi, fakeApi, ruleViolation, type FakeApi } from '../test/fixtures';
import { PortfoliosPage } from './PortfoliosPage';

async function open(code: string, api: FakeApi = fakeApi()) {
  render(<PortfoliosPage api={asApi(api)} />);
  const [node] = await screen.findAllByRole('button', { name: new RegExp(`^${code}Портфель`) });
  await userEvent.click(node!);
  return api;
}

function form(name: string) {
  return within(screen.getByRole('form', { name }));
}

describe('PortfoliosPage', () => {
  it('asks to pick a portfolio first', async () => {
    render(<PortfoliosPage api={asApi(fakeApi())} />);

    expect(await screen.findByText('Выберите портфель в дереве.')).toBeInTheDocument();
  });

  it('shows why the data did not load', async () => {
    const api = fakeApi();
    api.portfolios.mockRejectedValue(new ApiError(503, null));

    render(<PortfoliosPage api={asApi(api)} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('ошибкой 503');
  });

  it('shows the card of a physical portfolio', async () => {
    await open('F1-OFZ');

    const card = within(screen.getByRole('article'));
    expect(card.getByRole('heading', { level: 2 })).toHaveTextContent('F1-OFZ — Портфель F1-OFZ');
    expect(card.getByText('F1')).toBeInTheDocument();
    expect(card.getByText('A1')).toBeInTheDocument();
  });

  it('renames a portfolio and reloads', async () => {
    const api = await open('F1-BONDS');
    const rename = form('Код и название');

    await userEvent.clear(rename.getByLabelText('Название'));
    await userEvent.type(rename.getByLabelText('Название'), 'Облигации');
    await userEvent.click(rename.getByRole('button', { name: 'Сохранить' }));

    expect(api.updatePortfolio).toHaveBeenCalledWith('id-F1-BONDS', {
      code: 'F1-BONDS',
      name: 'Облигации',
    });
    expect(api.portfolios).toHaveBeenCalledTimes(2);
  });

  it('shows the rule the server refused', async () => {
    const api = fakeApi();
    api.updatePortfolio.mockRejectedValue(ruleViolation('Код портфеля «F2» уже занят.'));
    await open('F1-BONDS', api);
    const rename = form('Код и название');

    await userEvent.clear(rename.getByLabelText('Код'));
    await userEvent.type(rename.getByLabelText('Код'), 'F2');
    await userEvent.click(rename.getByRole('button', { name: 'Сохранить' }));

    expect(await rename.findByRole('alert')).toHaveTextContent('Код портфеля «F2» уже занят.');
  });

  it('creates a compound inside a fund', async () => {
    const api = await open('F1');
    const add = form('Новый портфель внутри');

    await userEvent.type(add.getByLabelText('Код'), 'F1-CORP');
    await userEvent.type(add.getByLabelText('Название'), 'Корпоративные');
    await userEvent.click(add.getByRole('button', { name: 'Создать' }));

    expect(api.createPortfolio).toHaveBeenCalledWith({
      code: 'F1-CORP',
      name: 'Корпоративные',
      kind: 'compound',
      parent_ids: ['id-F1'],
      account_id: null,
    });
    expect(add.getByLabelText('Код')).toHaveValue('');
  });

  it('creates a physical with an account of the fund', async () => {
    const api = await open('F1-BONDS');
    const add = form('Новый портфель внутри');

    await userEvent.selectOptions(add.getByLabelText('Тип'), 'physical');
    await userEvent.selectOptions(add.getByLabelText('Счёт'), 'id-A1-BANK');
    await userEvent.type(add.getByLabelText('Код'), 'F1-REPO');
    await userEvent.type(add.getByLabelText('Название'), 'РЕПО');
    await userEvent.click(add.getByRole('button', { name: 'Создать' }));

    expect(api.createPortfolio).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'physical', account_id: 'id-A1-BANK' }),
    );
  });

  it('keeps the form when creation is refused', async () => {
    const api = fakeApi();
    api.createPortfolio.mockRejectedValue(ruleViolation('Нельзя.'));
    await open('F2', api);
    const add = form('Новый портфель внутри');

    await userEvent.selectOptions(add.getByLabelText('Тип'), 'physical');
    await userEvent.type(add.getByLabelText('Код'), 'F2-X');
    await userEvent.type(add.getByLabelText('Название'), 'X');
    await userEvent.click(add.getByRole('button', { name: 'Создать' }));

    expect(await add.findByRole('alert')).toHaveTextContent('Нельзя.');
    expect(add.getByLabelText('Код')).toHaveValue('F2-X');
  });

  it('warns when a fund has no accounts for a physical', async () => {
    const api = await open('F1', fakeApi({ accounts: [] }));
    const add = form('Новый портфель внутри');

    await userEvent.selectOptions(add.getByLabelText('Тип'), 'physical');
    await userEvent.type(add.getByLabelText('Код'), 'F1-X');
    await userEvent.type(add.getByLabelText('Название'), 'X');
    await userEvent.click(add.getByRole('button', { name: 'Создать' }));

    expect(add.getByRole('option', { name: 'У фонда нет счетов' })).toBeInTheDocument();
    // Сервер ответит понятным правилом «у physical должна быть пара счетов».
    expect(api.createPortfolio).toHaveBeenCalledWith(expect.objectContaining({ account_id: null }));
  });

  it('shows an empty account choice for a physical without an account', async () => {
    const api = fakeApi({
      portfolios: [
        {
          id: 'id-F',
          code: 'F',
          name: 'Портфель F',
          kind: 'compound',
          is_fund: true,
          parent_ids: [],
          fund_id: 'id-F',
          account_id: null,
          treasury_id: null,
        },
        {
          id: 'id-P',
          code: 'P',
          name: 'Портфель P',
          kind: 'physical',
          is_fund: false,
          parent_ids: ['id-F'],
          fund_id: 'id-F',
          account_id: null,
          treasury_id: null,
        },
      ],
      accounts: [],
    });
    await open('P', api);

    const select = form('Пара счетов').getByLabelText('Счёт');
    expect(within(select).queryAllByRole('option')).toHaveLength(0);
    expect(within(screen.getByRole('article')).getByText('—')).toBeInTheDocument();
  });

  it('does not offer a physical inside a view', async () => {
    await open('BOOK');
    const add = form('Новый портфель внутри');

    expect(add.queryByRole('option', { name: /physical/ })).not.toBeInTheDocument();
    expect(add.getByText(/Physical создаётся внутри фонда/)).toBeInTheDocument();
  });

  it('links an existing portfolio', async () => {
    const api = await open('BOOK');
    const link = form('Включить существующий');

    await userEvent.selectOptions(link.getByLabelText('Портфель'), 'id-F1-TREASURY');
    await userEvent.click(link.getByRole('button', { name: 'Включить' }));

    expect(api.link).toHaveBeenCalledWith('id-BOOK', 'id-F1-TREASURY');
  });

  it('offers nothing to link when every portfolio is already inside', async () => {
    const api = fakeApi({
      portfolios: [
        {
          id: 'id-ONLY',
          code: 'ONLY',
          name: 'Портфель ONLY',
          kind: 'compound',
          is_fund: true,
          parent_ids: [],
          fund_id: 'id-ONLY',
          account_id: null,
          treasury_id: null,
        },
      ],
    });
    await open('ONLY', api);

    expect(screen.queryByRole('form', { name: 'Включить существующий' })).not.toBeInTheDocument();
  });

  it('removes a portfolio from one of its parents', async () => {
    const api = await open('F1-OFZ');

    await userEvent.click(screen.getByRole('button', { name: 'Исключить из BOOK' }));

    expect(api.unlink).toHaveBeenCalledWith('id-BOOK', 'id-F1-OFZ');
  });

  it('changes the account of a physical', async () => {
    const api = await open('F1-OFZ');
    const account = form('Пара счетов');

    await userEvent.selectOptions(account.getByLabelText('Счёт'), 'id-A1-BANK');
    await userEvent.click(account.getByRole('button', { name: 'Сменить счёт' }));

    expect(api.setAccount).toHaveBeenCalledWith('id-F1-OFZ', 'id-A1-BANK');
  });

  it('sets and clears the treasury of a fund', async () => {
    const api = await open('F1');
    const treasury = form('Казначейский портфель');

    await userEvent.click(treasury.getByRole('button', { name: 'Назначить' }));
    await userEvent.selectOptions(treasury.getByLabelText('Портфель'), '');
    await userEvent.click(treasury.getByRole('button', { name: 'Назначить' }));

    expect(api.setTreasury.mock.calls).toEqual([
      ['id-F1', 'id-F1-TREASURY'],
      ['id-F1', null],
    ]);
  });

  it('creates a fund and a view at the top level', async () => {
    const api = fakeApi();
    render(<PortfoliosPage api={asApi(api)} />);
    const create = within(await screen.findByRole('form', { name: 'Новый фонд или представление' }));

    await userEvent.type(create.getByLabelText('Код'), 'F3');
    await userEvent.type(create.getByLabelText('Название'), 'Фонд 3');
    await userEvent.click(create.getByRole('button', { name: 'Создать' }));
    await userEvent.type(create.getByLabelText('Код'), 'ALL');
    await userEvent.type(create.getByLabelText('Название'), 'Все фонды');
    await userEvent.click(create.getByLabelText('Это фонд'));
    await userEvent.click(create.getByRole('button', { name: 'Создать' }));

    expect(api.createPortfolio.mock.calls).toEqual([
      [{ code: 'F3', name: 'Фонд 3', kind: 'compound', is_fund: true }],
      [{ code: 'ALL', name: 'Все фонды', kind: 'compound', is_fund: false }],
    ]);
  });

  it('keeps the top-level form when creation is refused', async () => {
    const api = fakeApi();
    api.createPortfolio.mockRejectedValue(ruleViolation('Код занят.'));
    render(<PortfoliosPage api={asApi(api)} />);
    const create = within(await screen.findByRole('form', { name: 'Новый фонд или представление' }));

    await userEvent.type(create.getByLabelText('Код'), 'F1');
    await userEvent.type(create.getByLabelText('Название'), 'Дубль');
    await userEvent.click(create.getByRole('button', { name: 'Создать' }));

    expect(await create.findByRole('alert')).toHaveTextContent('Код занят.');
    expect(create.getByLabelText('Код')).toHaveValue('F1');
  });
});
