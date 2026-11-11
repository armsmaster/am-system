import type { AuditEvent } from './api';

/**
 * Перевод событий журнала на язык пользователя: что сделали, с чем
 * и что именно изменилось.
 */

const ACTIONS: Readonly<Record<string, string>> = {
  'portfolio.created': 'Создан портфель',
  'portfolio.updated': 'Изменён портфель',
  'portfolio.linked': 'Портфель включён в compound',
  'portfolio.unlinked': 'Портфель исключён из compound',
  'portfolio.account_changed': 'Сменён счёт портфеля',
  'fund.treasury_changed': 'Сменён казначейский портфель',
  'account.created': 'Создан счёт',
  'account.updated': 'Изменён счёт',
  'access.granted': 'Выдан доступ',
  'access.revoked': 'Отозван доступ',
  'instrument.loaded': 'Загружена бумага',
  'instrument.version_added': 'Новая версия бумаги',
  'calendar.day_set': 'Изменён торговый календарь',
  'calendar.day_cleared': 'Удалено исключение календаря',
  'manual_price.set': 'Введена ручная цена',
  'price_rule.changed': 'Изменено правило оценки',
  'prices.backfilled': 'Цены за прошлые дни',
  'settlement.updated': 'НКД и номинал на дату расчётов',
  'trade.entered': 'Введена сделка',
  'trade.amended': 'Исправлена сделка',
  'trade.cancelled': 'Отменена сделка',
  'trade.confirmed': 'Подтверждена сделка',
  'operation.entered': 'Проведена операция фонда',
  'operation.settled': 'Отмечен расчёт по паям',
  'operation.cancelled': 'Отменена операция фонда',
  'fee_rate.set': 'Изменена ставка комиссии от СЧА',
  'broker_tariff.set': 'Изменён тариф брокера',
  'statement.received': 'Получена выписка по счёту',
  'discrepancy.accepted': 'Принято расхождение сверки',
  'day.closed': 'Закрыт день фонда',
  'day.reopened': 'Открыт заново день фонда',
  'rating.added': 'Записан рейтинг',
  'rating.cancelled': 'Отменена запись о рейтинге',
  'classifier.created': 'Заведён классификатор',
  'classifier.updated': 'Изменён классификатор',
  'classifier.value_added': 'Добавлено значение классификатора',
  'classifier.value_renamed': 'Переименовано значение классификатора',
  'classification.set': 'Задано значение классификатора',
  'classification.cancelled': 'Отменено значение классификатора',
  'limit_set.saved': 'Сохранена версия набора лимитов',
  'breach.detected': 'Выявлено нарушение лимита',
  'breach.eliminated': 'Устранено нарушение лимита',
  'breach.reviewed': 'Записан вид нарушения',
  'breach.extended': 'Продлён срок устранения нарушения',
};

const FIELDS: Readonly<Record<string, string>> = {
  code: 'Код',
  name: 'Название',
  kind: 'Тип',
  is_fund: 'Это фонд',
  account_code: 'Счёт',
  treasury_code: 'Казначейский портфель',
  fund_code: 'Фонд',
  cash_kind: 'Денежный счёт',
  institution: 'Брокер или банк',
  cash_account: 'Номер денежного счёта',
  depo_account: 'Счёт депо',
  stock_market: 'Фондовый рынок',
  derivatives_market: 'Срочный рынок',
  short_name: 'Краткое название',
  board: 'Режим торгов',
  currency: 'Валюта',
  issuer: 'Эмитент',
  face_value: 'Номинал',
  initial_face_value: 'Начальный номинал',
  issue_date: 'Дата выпуска',
  maturity_date: 'Дата погашения',
  coupon_kind: 'Тип купона',
  coupon_frequency: 'Купонов в год',
  nominal_indexation: 'Индексация номинала',
  coupons: 'Купоны',
  amortizations: 'Амортизации',
  offers: 'Оферты',
  floater: 'Параметры флоатера',
  rate: 'ставка',
  margin_bp: 'маржа (бп)',
  capitalized: 'капитализация',
  is_trading: 'Торговый день',
  comment: 'Комментарий',
  value: 'Цена',
  valid_through: 'Действует по',
  price_types: 'Типы цен',
  dema_timeframe: 'Таймфрейм DEMA, мин',
  dema_half_life: 'Период полураспада DEMA, мин',
  spread_threshold_bp: 'Порог разброса, бп',
  portfolio: 'Портфель',
  secid: 'Бумага',
  status: 'Статус',
  side: 'Направление',
  quantity: 'Количество',
  price: 'Цена, % или ₽',
  accrued: 'НКД на бумагу',
  amount: 'Сумма',
  fee: 'Комиссия',
  trade_date: 'Дата сделки',
  settle_date: 'Дата расчётов',
  receiver: 'Получатель',
  op_date: 'Дата операции',
  units: 'Паи',
  planned_date: 'Плановая дата расчёта',
  settled_date: 'Расчёт отмечен',
  expense_kind: 'Вид расхода',
  fee_kind: 'Вид комиссии',
  period_end: 'Оплачено по',
  valid_from: 'Действует с',
  repo_rate: 'Ставка РЕПО',
  outright_fee: 'Комиссия купли-продажи, % от оборота без НКД',
  repo_fee: 'Комиссия РЕПО, % годовых',
  second_date: 'Вторая часть: дата',
  second_amount: 'Вторая часть: сумма',
  direction: 'Кто платит',
  opening_cash: 'Остаток на начало',
  closing_cash: 'Остаток на конец',
  trades: 'Сделок',
  movements: 'Движений денег',
  line: 'Строка сверки',
  system: 'В системе',
  statement: 'В выписке',
  first_day: 'С дня',
  last_day: 'По день',
  closed_through: 'Фонд закрыт по',
  action: 'Что произошло',
  agency: 'Агентство',
  rating: 'Рейтинг',
  target: 'К чему относится',
  values: 'Значения',
  description: 'Описание',
  class_value: 'Значение',
  version: 'Версия',
  document: 'Документ',
  limits: 'Лимиты',
  measured: 'Значение',
  breach_kind: 'Вид нарушения',
  deadline: 'Срок устранения',
  decision: 'Решение',
  eliminated_on: 'Устранено',
};

/** Значения, которые зависят от поля: «active» у нарушения — не «действует». */
const FIELD_VALUES: Readonly<Record<string, Readonly<Record<string, string>>>> = {
  breach_kind: { active: 'активное', passive: 'пассивное' },
};

/** Значения перечислений — словами. */
const VALUES: Readonly<Record<string, string>> = {
  broker: 'брокерский',
  bank: 'банковский',
  fixed: 'фиксированный',
  floater: 'флоатер',
  unknown: 'не определён',
  none: 'нет',
  key_rate: 'ключевая ставка ЦБ',
  ruonia: 'RUONIA',
  rusfar: 'RUSFAR',
  entered: 'введена',
  confirmed: 'подтверждена',
  cancelled: 'отменена',
  buy: 'покупка',
  sell: 'продажа',
  active: 'действует',
  unit_issue: 'выдача паёв',
  unit_redemption: 'погашение паёв',
  expense_accrual: 'начисление расхода',
  expense_payment: 'выплата расхода',
  cash_transfer: 'перевод денег',
  security_transfer: 'перевод бумаг',
  fee_payment: 'выплата вознаграждения',
  management: 'вознаграждение УК',
  specdep: 'специализированный депозитарий',
  registrar: 'регистратор',
  auditor: 'аудитор',
  exchange_broker: 'биржа и брокер',
  other: 'прочие',
  outright: 'покупка или продажа',
  repo: 'РЕПО с НКЦ',
  repo_contribution: 'компенсационный взнос по РЕПО',
  in: 'НКЦ — фонду',
  out: 'фонд — НКЦ',
  matched: 'сходится',
  mismatched: 'расходится',
  not_in_system: 'нет в системе',
  not_in_statement: 'нет в выписке',
  assigned: 'рейтинг присвоен или изменён',
  withdrawn: 'рейтинг отозван',
  unrated: 'рейтинга нет ни у одного агентства',
  acra: 'АКРА',
  expert_ra: 'Эксперт РА',
  nkr: 'НКР',
  nra: 'НРА',
  instrument: 'бумага',
  issuer: 'эмитент',
  open: 'открыто',
  eliminated: 'устранено',
};

/** Порядок полей — как в FIELDS: база данных хранит ключи события в своём порядке. */
const ORDER = Object.keys(FIELDS);

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

export interface Change {
  readonly field: string;
  readonly before: string;
  readonly after: string;
}

type State = Readonly<Record<string, unknown>>;
interface Ref {
  readonly code?: string;
  readonly username?: string;
  readonly subject?: string;
  readonly secid?: string;
}

export function actionLabel(type: string): string {
  return ACTIONS[type] ?? type;
}

/** С чем совершено действие: код портфеля или счёта, пользователь и портфель. */
export function objectLabel(event: AuditEvent): string {
  const data = event.data as {
    before?: State;
    after?: State;
    parent?: Ref;
    child?: Ref;
    user?: Ref;
    portfolio?: Ref;
    instrument?: Ref;
    issuer?: { readonly name: string };
    classifier?: { readonly name: string };
    trade?: { readonly secid: string; readonly portfolio: string | null };
    breach?: { readonly limit: string; readonly name: string };
    operation?: { readonly kind: string; readonly portfolio: string | null };
    instruments?: readonly { readonly secid: string }[];
    fund?: Ref;
    account?: Ref;
    portfolios?: readonly string[];
    date?: string;
    since?: string;
    version?: number;
    day?: string;
  };
  if (data.trade) {
    return `${data.trade.portfolio ?? '—'}: ${data.trade.secid}`;
  }
  if (data.breach) {
    return `${data.fund?.code ?? '—'}: ${data.breach.limit} — ${data.breach.name}`;
  }
  // Рейтинги и классификаторы: бумага или эмитент, у значения — ещё и классификатор.
  const subject = data.instrument?.secid ?? data.issuer?.name;
  if (data.classifier) {
    return subject ? `${subject}: ${data.classifier.name}` : data.classifier.name;
  }
  if (data.issuer) {
    return data.issuer.name;
  }
  if (data.fund) {
    return `${data.fund.code ?? ''}`;
  }
  if (data.portfolios) {
    // Тариф брокера — на счёт: в журнале — портфели этого счёта.
    return `счёт портфелей ${data.portfolios.join(', ')}`;
  }
  if (data.account && data.date) {
    // Сверка: выписка или принятое расхождение — по счёту за день.
    return `${data.account.code ?? ''}, ${show(data.date)}`;
  }
  if (data.instruments && data.since) {
    // Цены за прошлые дни: по ним сервис сделок пересчитывает оценку позиций.
    return `${data.instruments.map((i) => i.secid).join(', ')} — с ${show(data.since)}`;
  }
  if (data.operation) {
    return `${data.operation.portfolio ?? '—'}: ${show(data.operation.kind)}`;
  }
  if (data.instrument) {
    return data.version ? `${data.instrument.secid}, версия ${data.version}` : `${data.instrument.secid}`;
  }
  if (data.day) {
    return new Date(`${data.day}T00:00:00Z`).toLocaleDateString('ru-RU', { timeZone: 'UTC' });
  }
  if (data.child && data.parent) {
    return `${data.child.code} ↔ ${data.parent.code}`;
  }
  if (data.user && data.portfolio) {
    return `${data.user.username ?? data.user.subject}: ${data.portfolio.code}`;
  }
  const state = data.after ?? data.before;
  return typeof state?.code === 'string' ? state.code : event.entity_id;
}

/** Поля «было → стало». Для созданного объекта «было» — прочерк. */
export function changes(event: AuditEvent): Change[] {
  const before = (event.data.before ?? undefined) as State | undefined;
  const after = (event.data.after ?? undefined) as State | undefined;
  const shape = after ?? before;
  if (!shape) {
    return [];
  }
  // Идентификаторы нужны машинам; человеку журнал показывает коды (поля *_code).
  return Object.keys(shape)
    .filter((key) => key !== 'id' && !key.endsWith('_id'))
    .sort(byFieldOrder)
    .filter((key) => JSON.stringify(before?.[key]) !== JSON.stringify(after?.[key]))
    .map((key) => ({
      field: FIELDS[key] ?? key,
      before: before ? show(before[key], key) : '—',
      after: after ? show(after[key], key) : '—',
    }));
}

/** Известные поля — в порядке FIELDS, незнакомые — за ними по алфавиту. */
function byFieldOrder(a: string, b: string): number {
  const rank = (key: string) => {
    const index = ORDER.indexOf(key);
    return index === -1 ? ORDER.length : index;
  };
  return rank(a) - rank(b) || a.localeCompare(b);
}

function show(value: unknown, key?: string): string {
  if (value === null || value === undefined || value === '') {
    return '—';
  }
  const special = key ? FIELD_VALUES[key]?.[String(value)] : undefined;
  if (special) {
    return special;
  }
  if (typeof value === 'boolean') {
    return value ? 'да' : 'нет';
  }
  if (typeof value === 'object') {
    // Вложенные условия, например параметры флоатера: «ставка: RUONIA; маржа (бп): 130».
    return Object.entries(value)
      .map(([key, item]) => `${FIELDS[key] ?? key}: ${show(item)}`)
      .join('; ');
  }
  const date = ISO_DATE.exec(String(value));
  if (date) {
    return `${date[3]}.${date[2]}.${date[1]}`;
  }
  return VALUES[String(value)] ?? String(value);
}

/** Время по Москве: в нём работают биржа и пользователи. */
export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString('ru-RU', {
    timeZone: 'Europe/Moscow',
    dateStyle: 'short',
    timeStyle: 'medium',
  });
}
