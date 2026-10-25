import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './App';
import { startSession } from './auth/keycloak';
import { registerMicrofrontends } from './microfrontends';
import './styles.css';

registerMicrofrontends();

const root = createRoot(document.getElementById('root')!);

startSession().then(
  (session) =>
    root.render(
      <StrictMode>
        <App session={session} />
      </StrictMode>,
    ),
  (error: unknown) => {
    console.error('Login failed', error);
    root.render(<p role="alert">Не удалось подключиться к сервису входа. Обновите страницу.</p>);
  },
);
