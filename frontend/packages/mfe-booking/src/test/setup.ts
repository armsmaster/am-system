import '@testing-library/jest-dom/vitest';

import { cleanup, configure } from '@testing-library/react';
import { afterEach } from 'vitest';

// Под нагрузкой — все пакеты в одном прогоне — страница грузится дольше секунды
// по умолчанию: findBy… ждёт до 5 секунд, а не падает на медленной машине.
configure({ asyncUtilTimeout: 5000 });

afterEach(cleanup);
