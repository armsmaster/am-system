import type { Operation } from '../api';
import { formatDate, OPERATION_LABELS } from '../format';
import { GROUP_OF, settlementOf } from '../operations';

/** Вид операции — цветной меткой: паи — зелёная и красная, остальное — нейтральная. */
export function KindPill({ operation }: { readonly operation: Operation }) {
  const tone =
    operation.kind === 'unit_issue' ? 'issue' : operation.kind === 'unit_redemption' ? 'redeem' : GROUP_OF[operation.kind];
  return <span className={`pill op-${tone}`}>{OPERATION_LABELS[operation.kind]}</span>;
}

/** Расчёт по паям: ждёт, просрочен или отмечен; у остальных операций — ничего. */
export function SettlementPill({ operation, today }: { readonly operation: Operation; readonly today: string }) {
  if (operation.status !== 'active') {
    return null;
  }
  switch (settlementOf(operation, today)) {
    case 'settled':
      return <span className="pill settle-done">расчёт {formatDate(operation.settled_date)}</span>;
    case 'overdue':
      return <span className="pill settle-overdue">расчёт просрочен: план {formatDate(operation.planned_date)}</span>;
    case 'waiting':
      return <span className="pill settle-waiting">ждёт расчёта {formatDate(operation.planned_date)}</span>;
    default:
      return null;
  }
}
