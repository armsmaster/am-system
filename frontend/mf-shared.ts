/**
 * Зависимости, общие для shell и всех микрофронтендов.
 *
 * Они загружаются один раз (singleton). Два экземпляра React на странице
 * ломают хуки и контекст; два экземпляра роутера — навигацию: раздел
 * не увидел бы маршрут, который выбрал shell.
 */
export const sharedDependencies = {
  react: { singleton: true, requiredVersion: '^19.3.1' },
  'react-dom': { singleton: true, requiredVersion: '^19.3.1' },
  'react-router': { singleton: true, requiredVersion: '^7.18.4' },
};
