import type { ApiClient, Role } from '@am/platform';

export type Kind = 'compound' | 'physical';
export type CashKind = 'broker' | 'bank';

export interface Portfolio {
  readonly id: string;
  readonly code: string;
  readonly name: string;
  readonly kind: Kind;
  readonly is_fund: boolean;
  readonly parent_ids: readonly string[];
  readonly fund_id: string | null;
  readonly account_id: string | null;
  readonly treasury_id: string | null;
}

export interface Account {
  readonly id: string;
  readonly fund_id: string;
  readonly code: string;
  readonly name: string;
  readonly cash_kind: CashKind;
  readonly institution: string;
  readonly cash_account: string;
  readonly depo_account: string | null;
  readonly stock_market: boolean;
  readonly derivatives_market: boolean;
}

export interface User {
  readonly subject: string;
  readonly username: string;
  readonly full_name: string;
  readonly enabled: boolean;
  readonly roles: readonly Role[];
  readonly grant_ids: readonly string[];
}

export interface NewPortfolio {
  readonly code: string;
  readonly name: string;
  readonly kind: Kind;
  readonly is_fund?: boolean;
  readonly parent_ids?: readonly string[];
  readonly account_id?: string | null;
}

/** Тариф брокера на счёт (решение 151): ставки комиссии для загрузки из отчёта QUIK. */
export interface Tariff {
  /** % от оборота без НКД. */
  readonly outright_rate: string;
  /** % годовых от суммы первой части РЕПО. */
  readonly repo_rate: string;
  readonly valid_from: string;
  readonly comment: string | null;
  readonly author: string;
  readonly created_at: string;
}

export interface NewTariff {
  readonly outright_rate: string;
  readonly repo_rate: string;
  readonly valid_from: string;
  readonly comment: string | null;
}

export type AccountFields = Omit<Account, 'id' | 'fund_id'>;
export type NewAccount = AccountFields & { readonly fund_id: string };

const BASE = '/api/portfolio';
const BOOKING = '/api/booking';

/** Вызовы API, которыми пользуется админка: сервис портфелей и тарифы брокера в сервисе сделок. */
export function portfolioApi(client: ApiClient) {
  return {
    portfolios: () => client.get<Portfolio[]>(`${BASE}/portfolios`),
    accounts: () => client.get<Account[]>(`${BASE}/accounts`),
    users: () => client.get<User[]>(`${BASE}/users`),

    createPortfolio: (body: NewPortfolio) => client.post<Portfolio>(`${BASE}/portfolios`, body),
    updatePortfolio: (id: string, body: { code?: string; name?: string }) =>
      client.patch<Portfolio>(`${BASE}/portfolios/${id}`, body),
    link: (parentId: string, childId: string) =>
      client.put<null>(`${BASE}/portfolios/${parentId}/children/${childId}`),
    unlink: (parentId: string, childId: string) =>
      client.delete<null>(`${BASE}/portfolios/${parentId}/children/${childId}`),
    setAccount: (portfolioId: string, accountId: string) =>
      client.put<Portfolio>(`${BASE}/portfolios/${portfolioId}/account`, { account_id: accountId }),
    setTreasury: (fundId: string, portfolioId: string | null) =>
      client.put<Portfolio>(`${BASE}/funds/${fundId}/treasury`, { portfolio_id: portfolioId }),

    createAccount: (body: NewAccount) => client.post<Account>(`${BASE}/accounts`, body),
    updateAccount: (id: string, body: Partial<AccountFields>) =>
      client.patch<Account>(`${BASE}/accounts/${id}`, body),

    tariffs: (accountId: string) => client.get<Tariff[]>(`${BOOKING}/accounts/${accountId}/tariffs`),
    setTariff: (accountId: string, body: NewTariff) =>
      client.post<Tariff[]>(`${BOOKING}/accounts/${accountId}/tariffs`, body),

    grant: (subject: string, portfolioId: string) =>
      client.put<null>(`${BASE}/users/${subject}/grants/${portfolioId}`),
    revoke: (subject: string, portfolioId: string) =>
      client.delete<null>(`${BASE}/users/${subject}/grants/${portfolioId}`),
  };
}

export type PortfolioApi = ReturnType<typeof portfolioApi>;
