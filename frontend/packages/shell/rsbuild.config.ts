import { pluginModuleFederation } from '@module-federation/rsbuild-plugin';
import { defineConfig } from '@rsbuild/core';
import { pluginReact } from '@rsbuild/plugin-react';

import { sharedDependencies } from '../../mf-shared';

export default defineConfig({
  plugins: [
    pluginReact(),
    pluginModuleFederation({
      name: 'shell',
      // Remote-ы регистрируются при запуске: см. src/microfrontends.ts.
      remotes: {},
      shared: sharedDependencies,
      dts: false,
    }),
  ],
  source: {
    entry: { index: './src/index.ts' },
  },
  html: {
    title: 'AM System',
  },
  server: {
    port: 3000,
    // Локальная разработка: API и Keycloak — через шлюз из docker compose.
    proxy: {
      '/api': 'http://localhost:8080',
    },
  },
});
