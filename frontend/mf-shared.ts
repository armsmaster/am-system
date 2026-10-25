/**
 * Зависимости, общие для shell и всех микрофронтендов.
 *
 * React загружается один раз (singleton): два экземпляра React на странице
 * ломают хуки и контекст.
 */
export const sharedDependencies = {
  react: { singleton: true, requiredVersion: '^19.3.1' },
  'react-dom': { singleton: true, requiredVersion: '^19.3.1' },
};
