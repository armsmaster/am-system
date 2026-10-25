import { createRoot } from 'react-dom/client';

// Сам по себе микрофронтенд не открывают: его загружает shell.
const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <p>Микрофронтенд «Администрирование» работает внутри shell: откройте приложение.</p>,
  );
}
