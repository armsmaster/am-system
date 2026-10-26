import { pluginModuleFederation } from '@module-federation/rsbuild-plugin';
import { defineConfig } from '@rsbuild/core';
import { pluginReact } from '@rsbuild/plugin-react';

import { sharedDependencies } from '../../mf-shared';

export default defineConfig(({ envMode }) => ({
  plugins: [
    pluginReact(),
    pluginModuleFederation({
      name: 'mfe_audit',
      exposes: { './App': './src/App.tsx' },
      shared: sharedDependencies,
      dts: false,
    }),
  ],
  source: {
    entry: { index: './src/index.ts' },
  },
  output: {
    // Шлюз публикует микрофронтенд под /mfe/audit/; в разработке — свой порт.
    assetPrefix: envMode === 'development' ? 'http://localhost:3002/' : '/mfe/audit/',
  },
  server: {
    port: 3002,
    headers: { 'Access-Control-Allow-Origin': '*' },
  },
}));
