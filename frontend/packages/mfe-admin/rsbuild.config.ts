import { pluginModuleFederation } from '@module-federation/rsbuild-plugin';
import { defineConfig } from '@rsbuild/core';
import { pluginReact } from '@rsbuild/plugin-react';

import { sharedDependencies } from '../../mf-shared';

export default defineConfig(({ envMode }) => ({
  plugins: [
    pluginReact(),
    pluginModuleFederation({
      name: 'mfe_admin',
      exposes: { './App': './src/App.tsx' },
      shared: sharedDependencies,
      dts: false,
    }),
  ],
  source: {
    entry: { index: './src/index.ts' },
  },
  output: {
    // Шлюз публикует микрофронтенд под /mfe/admin/; в разработке — свой порт.
    assetPrefix: envMode === 'development' ? 'http://localhost:3001/' : '/mfe/admin/',
  },
  server: {
    port: 3001,
    headers: { 'Access-Control-Allow-Origin': '*' },
  },
}));
