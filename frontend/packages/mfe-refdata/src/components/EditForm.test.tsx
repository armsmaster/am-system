import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { Instrument } from '../api';
import {
  asApi,
  fakeApi,
  floaterParams,
  instrument,
  params,
  ruleViolation,
  version,
  type FakeApi,
} from '../test/fixtures';
import { changesOf, EditForm } from './EditForm';

const TODAY = '2026-10-26';

function renderForm(subject: Instrument = instrument(), api: FakeApi = fakeApi()) {
  const onDone = vi.fn();
  render(<EditForm instrument={subject} api={asApi(api)} onDone={onDone} today={TODAY} />);
  return { api, onDone };
}

async function save(comment = 'Маржа из решения о выпуске') {
  await userEvent.type(screen.getByLabelText('Комментарий — что и почему'), comment);
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить версию' }));
}

describe('changesOf', () => {
  const p = floaterParams({ floater: { rate: 'key_rate', margin_bp: '130.0', capitalized: false } });
  const draft = {
    short_name: 'ОФЗ 29025',
    maturity_date: '2036-08-13',
    coupon_kind: 'floater' as const,
    nominal_indexation: 'none' as const,
    rate: 'key_rate' as const,
    margin_bp: '130.0',
    capitalized: false,
  };

  it('is empty when nothing changed', () => {
    expect(changesOf(p, draft)).toEqual({});
  });

  it('treats 130 and 130.0 as the same margin', () => {
    expect(changesOf(p, { ...draft, margin_bp: ' 130 ' })).toEqual({});
  });

  it('sends only changed fields', () => {
    expect(
      changesOf(p, {
        ...draft,
        short_name: ' ОФЗ-ПК 29025 ',
        maturity_date: '',
        nominal_indexation: 'ruonia',
        capitalized: true,
      }),
    ).toEqual({
      short_name: 'ОФЗ-ПК 29025',
      maturity_date: null,
      nominal_indexation: 'ruonia',
      floater: { rate: 'key_rate', margin_bp: '130.0', capitalized: true },
    });
  });

  it('drops floater terms when the bond is not a floater', () => {
    expect(changesOf(p, { ...draft, coupon_kind: 'fixed' })).toEqual({
      coupon_kind: 'fixed',
      floater: null,
    });
  });

  it('sees a change of the rate', () => {
    expect(changesOf(p, { ...draft, rate: 'ruonia' })).toEqual({
      floater: { rate: 'ruonia', margin_bp: '130.0', capitalized: false },
    });
  });

  it('adds floater terms for the first time', () => {
    expect(changesOf(floaterParams(), { ...draft, margin_bp: '95' })).toEqual({
      floater: { rate: 'key_rate', margin_bp: '95', capitalized: false },
    });
  });
});

describe('EditForm', () => {
  it('fills in floater terms as a new version from today', async () => {
    const { api, onDone } = renderForm(instrument([version(1, { params: floaterParams() })]));

    expect(screen.getByLabelText('Тип купона')).toHaveValue('floater');
    expect(screen.getByLabelText('Действует с')).toHaveValue(TODAY);
    await userEvent.selectOptions(screen.getByLabelText('Плавающая ставка'), 'ruonia');
    await userEvent.type(screen.getByLabelText('Маржа к ставке, бп'), '130');
    await userEvent.click(screen.getByLabelText(/капитализируется внутри купонного периода/));
    fireEvent.change(screen.getByLabelText('Действует с'), { target: { value: '2021-06-16' } });
    await save();

    expect(api.edit).toHaveBeenCalledWith('i-1', {
      valid_from: '2021-06-16',
      comment: 'Маржа из решения о выпуске',
      changes: { floater: { rate: 'ruonia', margin_bp: '130', capitalized: true } },
      unpin: [],
    });
    expect(onDone).toHaveBeenCalledOnce();
  });

  it('turns a bond into a floater and back', async () => {
    renderForm();

    expect(screen.queryByLabelText('Маржа к ставке, бп')).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText('Тип купона'), 'floater');
    expect(screen.getByLabelText('Маржа к ставке, бп')).toHaveValue('');
    expect(screen.getByLabelText('Плавающая ставка')).toHaveValue('key_rate');
  });

  it('edits the short name, maturity and indexation', async () => {
    const { api } = renderForm();

    await userEvent.clear(screen.getByLabelText('Краткое название'));
    await userEvent.type(screen.getByLabelText('Краткое название'), 'ОФЗ-ПД 26238');
    fireEvent.change(screen.getByLabelText('Дата погашения'), { target: { value: '2041-05-16' } });
    await userEvent.selectOptions(screen.getByLabelText('Индексация номинала'), 'ruonia');
    await save('Исправление по проспекту');

    expect(api.edit).toHaveBeenCalledWith('i-1', {
      valid_from: TODAY,
      comment: 'Исправление по проспекту',
      changes: { short_name: 'ОФЗ-ПД 26238', maturity_date: '2041-05-16', nominal_indexation: 'ruonia' },
      unpin: [],
    });
  });

  it('starts no earlier than the latest version', () => {
    renderForm(instrument([version(1), version(2, { valid_from: '2026-12-01' })]));

    expect(screen.getByLabelText('Действует с')).toHaveValue('2026-12-01');
    expect(screen.getByLabelText('Действует с')).toHaveAttribute('min', '2026-12-01');
  });

  it('keeps today when the latest version started earlier', () => {
    renderForm(instrument([version(1), version(2, { valid_from: '2026-10-01' })]));

    expect(screen.getByLabelText('Действует с')).toHaveValue(TODAY);
  });

  it('returns pinned fields to the ISS values', async () => {
    const pinned = version(2, {
      valid_from: '2026-10-01',
      pinned: ['short_name', 'maturity_date'],
      params: params({ short_name: 'ОФЗ-ПД 26238' }),
    });
    const { api } = renderForm(instrument([version(1), pinned]));

    await userEvent.click(screen.getByRole('checkbox', { name: 'Краткое название' }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'Дата погашения' }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'Дата погашения' }));
    await save('Вернули название из ISS');

    expect(api.edit).toHaveBeenCalledWith('i-1', expect.objectContaining({ unpin: ['short_name'], changes: {} }));
  });

  it('offers only the name for a fund share', () => {
    renderForm(
      instrument([version(1, { params: params({ coupon_kind: null, maturity_date: null }) })], {
        kind: 'fund_share',
      }),
    );

    expect(screen.getByLabelText('Краткое название')).toBeInTheDocument();
    expect(screen.queryByLabelText('Тип купона')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Дата погашения')).not.toBeInTheDocument();
    expect(screen.queryByText('Вернуть значение из ISS')).not.toBeInTheDocument();
  });

  it('shows a bond without a known coupon kind as unknown', () => {
    renderForm(instrument([version(1, { params: params({ coupon_kind: null }) })]));

    expect(screen.getByLabelText('Тип купона')).toHaveValue('unknown');
  });

  it('shows why the service refused', async () => {
    const api = fakeApi();
    api.edit.mockRejectedValue(ruleViolation('Изменений нет — новая версия не нужна.'));
    const { onDone } = renderForm(undefined, api);

    await save();

    expect(await screen.findByRole('alert')).toHaveTextContent('Изменений нет');
    expect(onDone).not.toHaveBeenCalled();
  });

  it('uses today in Moscow by default', () => {
    render(<EditForm instrument={instrument()} api={asApi(fakeApi())} onDone={() => undefined} />);

    expect(screen.getByLabelText('Действует с')).toHaveValue(
      new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/Moscow' }),
    );
  });
});
