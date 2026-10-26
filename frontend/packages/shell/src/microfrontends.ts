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
  env: Pick<ImportMetaEnv, 'PUBLIC_MFE_ADMIN_URL' | 'PUBLIC_MFE_AUDIT_URL'> = import.meta.env,
  origin: string = window.location.origin,
) {
  const manifest = (devUrl: string | undefined, path: string) =>
    new URL('mf-manifest.json', devUrl || `${origin}${path}`).href;
  return [
    { name: 'mfe_admin', entry: manifest(env.PUBLIC_MFE_ADMIN_URL, '/mfe/admin/') },
    { name: 'mfe_audit', entry: manifest(env.PUBLIC_MFE_AUDIT_URL, '/mfe/audit/') },
  ];
}

export function registerMicrofrontends(): void {
  registerRemotes(microfrontendRemotes());
}

export const loadAdmin: MicrofrontendLoader = () => load('mfe_admin/App');
export const loadAudit: MicrofrontendLoader = () => load('mfe_audit/App');

async function load(id: string): Promise<MicrofrontendModule> {
  const module = await loadRemote<MicrofrontendModule>(id);
  if (!module) {
    throw new Error(`Microfrontend ${id} is not available`);
  }
  return module;
}
