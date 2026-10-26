import type { Portfolio } from '../api';

interface TreeProps {
  readonly portfolios: readonly Portfolio[];
  readonly selectedId: string | null;
  readonly onSelect: (id: string) => void;
}

/**
 * Дерево портфелей. Структура — граф: портфель с несколькими родителями
 * показан под каждым из них и помечен «в N местах».
 */
export function PortfolioTree({ portfolios, selectedId, onSelect }: TreeProps) {
  const roots = portfolios.filter((p) => p.parent_ids.length === 0);
  if (roots.length === 0) {
    return <p className="muted">Портфелей пока нет. Начните с фонда.</p>;
  }
  const children = groupByParent(portfolios);
  const treasuries = new Set(portfolios.map((p) => p.treasury_id).filter(Boolean));

  function renderNode(portfolio: Portfolio, path: string) {
    const nested = children.get(portfolio.id) ?? [];
    const selected = portfolio.id === selectedId;
    return (
      <li key={path} role="treeitem" aria-selected={selected}>
        <button
          type="button"
          className={selected ? 'tree-item selected' : 'tree-item'}
          onClick={() => onSelect(portfolio.id)}
        >
          <span className="code">{portfolio.code}</span>
          <span className="muted">{portfolio.name}</span>
          <span className="badge">{kindLabel(portfolio)}</span>
          {treasuries.has(portfolio.id) && <span className="badge">казначейство</span>}
          {portfolio.parent_ids.length > 1 && (
            <span className="badge">в {portfolio.parent_ids.length} местах</span>
          )}
        </button>
        {nested.length > 0 && (
          <ul role="group">{nested.map((child) => renderNode(child, `${path}/${child.id}`))}</ul>
        )}
      </li>
    );
  }

  return (
    <ul className="tree" role="tree" aria-label="Портфели">
      {roots.map((root) => renderNode(root, root.id))}
    </ul>
  );
}

export function kindLabel(portfolio: Portfolio): string {
  if (portfolio.is_fund) {
    return 'фонд';
  }
  return portfolio.kind === 'physical' ? 'physical' : 'compound';
}

function groupByParent(portfolios: readonly Portfolio[]): Map<string, Portfolio[]> {
  const result = new Map<string, Portfolio[]>();
  for (const portfolio of portfolios) {
    for (const parentId of portfolio.parent_ids) {
      result.set(parentId, [...(result.get(parentId) ?? []), portfolio]);
    }
  }
  return result;
}
