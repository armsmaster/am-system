import '@testing-library/jest-dom/vitest';

import { cleanup, configure } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

// Под нагрузкой — все пакеты в одном прогоне — страница грузится дольше секунды
// по умолчанию: findBy… ждёт до 5 секунд, а не падает на медленной машине.
configure({ asyncUtilTimeout: 5000 });

afterEach(cleanup);

// Canvas в jsdom не рисуется: по умолчанию график — заглушка. Что именно рисуем,
// проверяют NavChart.test и NavPage.test своими заглушками.
vi.mock('lightweight-charts', () => ({
  LineSeries: 'line',
  CrosshairMode: { Magnet: 1 },
  LineStyle: { Solid: 0, Dotted: 1, Dashed: 2 },
  createChart: () => ({
    addSeries: () => ({ setData: () => undefined }),
    timeScale: () => ({ fitContent: () => undefined }),
    remove: () => undefined,
  }),
}));
