import { createApiClient, ROLE_LABELS, type Session } from '@am/platform';
import { useEffect, useState } from 'react';
import { Link } from 'react-router';

import { Icon } from '../components/Icon';
import { SECTIONS, type Access } from '../sections';

type Status = 'loading' | 'ready' | 'failed';

/** Приветствие по времени суток по Москве: в нём работают биржа и пользователи. */
function greeting(now: Date): string {
  const hour = Number(now.toLocaleString('en-GB', { timeZone: 'Europe/Moscow', hour: '2-digit', hourCycle: 'h23' }));
  if (hour < 5) {
    return 'Доброй ночи';
  }
  if (hour < 12) {
    return 'Доброе утро';
  }
  return hour < 18 ? 'Добрый день' : 'Добрый вечер';
}

/**
 * Стартовая страница: приветствие, роли, сегодняшняя дата и разделы, доступные
 * пользователю. Внизу — связь с сервисами: вход → шлюз → сервис → проверка токена.
 */
export function Home({
  session,
  access,
  now = new Date(),
}: {
  readonly session: Session;
  readonly access: Access;
  readonly now?: Date;
}) {
  const [status, setStatus] = useState<Status>('loading');

  useEffect(() => {
    let active = true;
    createApiClient(session)
      .get('/api/portfolio/me')
      .then(
        (): Status => 'ready',
        (): Status => 'failed',
      )
      .then((next) => {
        // Ответ мог прийти, когда пользователь уже ушёл со страницы.
        if (active) {
          setStatus(next);
        }
      });
    return () => {
      active = false;
    };
  }, [session]);

  const roles = [...session.roles].map((role) => ROLE_LABELS[role]);
  const today = now.toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow' });
  const sections = SECTIONS.filter((s) => s.visible(access));

  return (
    <section className="home">
      <h1>
        {greeting(now)}, {session.displayName}
      </h1>
      <p className="muted">{[...roles, today].join(' · ')}</p>
      {/* Справочник и цены видны всем: список разделов не бывает пустым. */}
      <ul className="section-cards" aria-label="Разделы">
        {sections.map((s) => (
          <li key={s.path}>
            <Link to={s.path} className="section-card">
              <Icon name={s.icon} size={22} />
              <span className="section-card-title">{s.label}</span>
              <span className="muted">{s.description}</span>
            </Link>
          </li>
        ))}
      </ul>
      {status === 'loading' && <p className="status muted">Проверяем связь с сервисами…</p>}
      {status === 'ready' && (
        <p className="status muted">
          <span className="dot dot-ok" aria-hidden="true" />
          Сервисы на связи
        </p>
      )}
      {status === 'failed' && (
        <p role="alert" className="notice notice-error">
          Сервис портфелей недоступен — данные разделов могут не загрузиться.
        </p>
      )}
    </section>
  );
}
