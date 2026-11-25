import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './App';
import { AuthGate } from './auth/AuthGate';
import { registerMicrofrontends } from './microfrontends';
import './styles.css';

registerMicrofrontends();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthGate>{(session) => <App session={session} />}</AuthGate>
  </StrictMode>,
);
