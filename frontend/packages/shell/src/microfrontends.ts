import type { MicrofrontendProps } from '@am/platform';
import { loadRemote, registerRemotes } from '@module-federation/enhanced/runtime';
import type { ComponentType } from 'react';

export interface MicrofrontendModule {
  default: ComponentType<MicrofrontendProps>;
}

export type MicrofrontendLoader = () => Promise<MicrofrontendModule>;

/**
 * Реестр микрофронтендов: где лежит манифест каждого remote.
 *
 * Адреса вычисляются при запуске в браузере, а не при сборке, поэтому
 * один и тот же образ shell работает на любом адресе. Для локальной
 * разработки адрес remote задаётся переменной PUBLIC_MFE_<ИМЯ>_URL.
 */
export function microfrontendRemotes(
  env: Pick<ImportMetaEnv, 'PUBLIC_MFE_ADMIN_URL'> = import.meta.env,
  origin: string = window.location.origin,
) {
  return [
    {
      name: 'mfe_admin',
      entry: new URL('mf-manifest.json', env.PUBLIC_MFE_ADMIN_URL || `${origin}/mfe/admin/`)
        .href,
    },
  ];
}

export function registerMicrofrontends(): void {
  registerRemotes(microfrontendRemotes());
}

export const loadAdmin: MicrofrontendLoader = () => load('mfe_admin/App');

async function load(id: string): Promise<MicrofrontendModule> {
  const module = await loadRemote<MicrofrontendModule>(id);
  if (!module) {
    throw new Error(`Microfrontend ${id} is not available`);
  }
  return module;
}
