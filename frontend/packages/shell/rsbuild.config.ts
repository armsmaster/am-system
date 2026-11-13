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
    // Знак AM System: SVG — современным браузерам, ICO из public/ — остальным,
    // PNG 180×180 — для закладки на экране iPhone.
    favicon: './src/assets/favicon.svg',
    appIcon: {
      icons: [{ src: './src/assets/apple-touch-icon.png', size: 180, target: 'apple-touch-icon' }],
    },
    tags: [{ tag: 'link', attrs: { rel: 'icon', href: '/favicon.ico', sizes: '32x32' }, head: true, append: false }],
  },
  server: {
    port: 3000,
    // Локальная разработка: API и Keycloak — через шлюз из docker compose.
    proxy: {
      '/api': 'http://localhost:8080',
    },
  },
});
