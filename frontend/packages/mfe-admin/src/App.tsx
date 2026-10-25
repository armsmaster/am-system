import { hasRole, type MicrofrontendProps } from '@am/platform';

/**
 * Администрирование: портфели, фонды, счета и права доступа.
 * Пока заглушка — наполняется на шаге 1 вместе с сервисом portfolio.
 */
export default function AdminApp({ session }: MicrofrontendProps) {
  // Сервер всё равно проверит права; здесь — только чтобы не показывать лишнего.
  if (!hasRole(session, 'admin')) {
    return <p role="alert">Раздел доступен только администратору.</p>;
  }

  return (
    <section>
      <h1>Администрирование</h1>
      <p>Здесь появятся дерево портфелей, фонды, счета и права доступа пользователей.</p>
    </section>
  );
}
