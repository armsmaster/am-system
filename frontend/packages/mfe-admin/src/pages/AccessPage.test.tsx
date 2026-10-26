import { ApiError } from '@am/platform';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { asApi, fakeApi, USERS } from '../test/fixtures';
import { AccessPage } from './AccessPage';

describe('AccessPage', () => {
  it('shows users, roles and current grants', async () => {
    render(<AccessPage api={asApi(fakeApi())} />);

    const trader = (await screen.findByText('trader')).closest('tr')!;
    expect(within(trader).getByText('Трейдинг')).toBeInTheDocument();
    expect(within(trader).getByRole('list', { name: 'Доступы trader' })).toHaveTextContent('F1-BONDS');
    const admin = screen.getByText('admin').closest('tr')!;
    expect(within(admin).getByText('видит всё')).toBeInTheDocument();
    const ops = screen.getAllByText('ops')[0]!.closest('tr')!;
    expect(within(ops).getAllByRole('cell')[1]).toHaveTextContent('—');
  });

  it('grants access to a compound', async () => {
    const api = fakeApi();
    render(<AccessPage api={asApi(api)} />);
    const grant = within(await screen.findByRole('form', { name: 'Выдать доступ ops' }));

    await userEvent.selectOptions(grant.getByLabelText('Портфель'), 'id-BOOK');
    await userEvent.click(grant.getByRole('button', { name: 'Выдать' }));

    expect(api.grant).toHaveBeenCalledWith('u-ops', 'id-BOOK');
    // В список выдачи попадают только compound.
    expect(grant.queryByRole('option', { name: /F1-OFZ/ })).not.toBeInTheDocument();
  });

  it('revokes access and shows a refusal', async () => {
    const api = fakeApi();
    api.revoke.mockRejectedValue(new ApiError(404, null));
    render(<AccessPage api={asApi(api)} />);

    await userEvent.click(await screen.findByRole('button', { name: 'Отозвать F1-BONDS у trader' }));

    expect(api.revoke).toHaveBeenCalledWith('u-trader', 'id-F1-BONDS');
    expect(await screen.findByRole('alert')).toHaveTextContent('не найден');
  });

  it('hides the grant form when everything is granted', async () => {
    const compounds = ['id-BOOK', 'id-F1', 'id-F1-BONDS', 'id-F2'];
    const users = [{ ...USERS[1]!, grant_ids: [...compounds, 'id-GONE'] }];
    render(<AccessPage api={asApi(fakeApi({ users }))} />);

    const grants = await screen.findByRole('list', { name: 'Доступы trader' });
    expect(screen.queryByRole('form')).not.toBeInTheDocument();
    // Доступ к портфелю, которого больше нет в списке, показан по идентификатору.
    expect(grants).toHaveTextContent('id-GONE');
  });
});
