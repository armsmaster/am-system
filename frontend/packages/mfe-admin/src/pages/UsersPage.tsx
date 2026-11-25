import { errorMessage, ROLE_LABELS, ROLES, type Role } from '@am/platform';
import { Drawer, ErrorNote, FilterChips, LoadedView, PageHeader, useAction, useLoad } from '@am/ui';
import { useCallback, useEffect, useState, type FormEvent } from 'react';

import { inviteUrl, type IdentityApi, type InviteLink, type InviteTtl, type Person, type UserStatus } from '../api';

/**
 * Пользователи (решение 170): кто входит в систему и с какими ролями. Новый
 * пользователь получает ссылку: по ней он задаёт пароль и сразу входит. Та же
 * ссылка — при забытом пароле. Пользователи живут в Keycloak.
 */

const ROLE_HINTS: Readonly<Record<Role, string>> = {
  trading: 'Сделки и позиции портфелей, к которым выдан доступ',
  operations: 'Операции фонда, расчёты, сверка с брокером',
  risk: 'Рейтинги, классификаторы, лимиты',
  admin: 'Всё, включая пользователей и доступы',
};

const TTLS: readonly InviteTtl[] = [1, 3, 7];
const TTL_LABELS: Readonly<Record<InviteTtl, string>> = { 1: '1 день', 3: '3 дня', 7: '7 дней' };

type Filter = 'all' | 'active' | 'waiting' | 'blocked';

const DATE_TIME = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

const when = (iso: string) => DATE_TIME.format(new Date(iso));

type Panel =
  | { readonly kind: 'create' }
  | { readonly kind: 'roles' | 'link' | 'block'; readonly person: Person }
  | { readonly kind: 'issued'; readonly username: string; readonly invite: InviteLink; readonly created: boolean };

export function UsersPage({ api, self }: { readonly api: IdentityApi; readonly self: string }) {
  const load = useCallback(() => api.people(), [api]);
  const [state, reload] = useLoad(load);
  const [filter, setFilter] = useState<Filter>('all');
  const [panel, setPanel] = useState<Panel | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const close = useCallback(() => setPanel(null), []);

  async function unblock(person: Person) {
    setProblem(null);
    try {
      await api.setEnabled(person.id, true);
      reload();
    } catch (error) {
      setProblem(errorMessage(error));
    }
  }

  return (
    <section>
      <PageHeader
        title="Пользователи"
        actions={
          <button type="button" className="primary" onClick={() => setPanel({ kind: 'create' })}>
            Новый пользователь
          </button>
        }
      />
      <ErrorNote message={problem} />
      <LoadedView state={state}>
        {(people) => {
          const shown = people.filter((p) => matches(p.status, filter));
          return (
            <>
              <div className="toolbar">
                <FilterChips
                  label="Статус"
                  value={filter}
                  onChange={setFilter}
                  choices={[
                    { value: 'all', label: 'Все', count: people.length },
                    { value: 'active', label: 'Активные', count: count(people, 'active') },
                    { value: 'waiting', label: 'Ждут входа', count: count(people, 'waiting') },
                    { value: 'blocked', label: 'Заблокированные', count: count(people, 'blocked') },
                  ]}
                />
              </div>
              {shown.length === 0 ? (
                <p className="muted">Таких пользователей нет.</p>
              ) : (
                <div className="people-scroll">
                  <table className="data people">
                    <thead>
                      <tr>
                        <th>Пользователь</th>
                        <th>Email</th>
                        <th>Роли</th>
                        <th>Статус</th>
                        <th>Последний вход</th>
                        <th aria-label="Действия" />
                      </tr>
                    </thead>
                    <tbody>
                      {shown.map((person) => (
                        <tr key={person.id} className={person.enabled ? undefined : 'blocked-row'}>
                          <td>
                            <div>{person.full_name || person.username}</div>
                            <div className="muted mono">{person.username}</div>
                          </td>
                          <td>{person.email ?? <span className="muted">—</span>}</td>
                          <td>
                            {person.roles.length === 0 ? (
                              <span className="muted">нет ролей</span>
                            ) : (
                              <ul className="role-chips" aria-label={`Роли ${person.username}`}>
                                {person.roles.map((role) => (
                                  <li key={role} className={`role-${role}`}>
                                    {ROLE_LABELS[role]}
                                  </li>
                                ))}
                              </ul>
                            )}
                          </td>
                          <td>
                            <Status person={person} />
                          </td>
                          <td>{person.last_login_at ? when(person.last_login_at) : <span className="muted">—</span>}</td>
                          <td className="row-actions">
                            <RowMenu
                              person={person}
                              self={person.username === self}
                              onPick={(kind) => setPanel({ kind, person })}
                              onUnblock={() => void unblock(person)}
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          );
        }}
      </LoadedView>
      {panel?.kind === 'create' && (
        <Drawer title="Новый пользователь" open onClose={close}>
          <CreateForm
            api={api}
            onCreated={(username, invite) => {
              reload();
              setPanel({ kind: 'issued', username, invite, created: true });
            }}
          />
        </Drawer>
      )}
      {panel?.kind === 'roles' && (
        <Drawer title={`Роли: ${nameOf(panel.person)}`} open onClose={close}>
          <RolesForm
            api={api}
            person={panel.person}
            self={panel.person.username === self}
            onDone={() => {
              reload();
              close();
            }}
          />
        </Drawer>
      )}
      {panel?.kind === 'link' && (
        <Drawer title="Новая ссылка для входа" open onClose={close}>
          <LinkForm
            api={api}
            person={panel.person}
            onIssued={(invite) => {
              reload();
              setPanel({ kind: 'issued', username: panel.person.username, invite, created: false });
            }}
          />
        </Drawer>
      )}
      {panel?.kind === 'block' && (
        <Drawer title="Заблокировать пользователя" open onClose={close}>
          <BlockForm
            api={api}
            person={panel.person}
            onDone={() => {
              reload();
              close();
            }}
          />
        </Drawer>
      )}
      {panel?.kind === 'issued' && (
        <Drawer title={panel.created ? 'Пользователь создан' : 'Ссылка для входа'} open onClose={close}>
          <Issued username={panel.username} invite={panel.invite} created={panel.created} onDone={close} />
        </Drawer>
      )}
    </section>
  );
}

function matches(status: UserStatus, filter: Filter): boolean {
  switch (filter) {
    case 'all':
      return true;
    case 'waiting':
      return status === 'pending' || status === 'expired';
    default:
      return status === filter;
  }
}

const count = (people: readonly Person[], filter: Filter) => people.filter((p) => matches(p.status, filter)).length;

const nameOf = (person: Person) => person.full_name || person.username;

function Status({ person }: { readonly person: Person }) {
  switch (person.status) {
    case 'active':
      return <span className="status-chip status-active">активен</span>;
    case 'blocked':
      return <span className="status-chip status-blocked">заблокирован</span>;
    case 'pending':
      return (
        <>
          <span className="status-chip status-pending">ждёт первого входа</span>
          <div className="muted status-note">ссылка до {when(person.invite_expires_at!)}</div>
        </>
      );
    case 'expired':
      return (
        <>
          <span className="status-chip status-expired">ссылка истекла</span>
          <div className="muted status-note">выдайте новую</div>
        </>
      );
  }
}

function RowMenu({
  person,
  self,
  onPick,
  onUnblock,
}: {
  readonly person: Person;
  readonly self: boolean;
  readonly onPick: (kind: 'roles' | 'link' | 'block') => void;
  readonly onUnblock: () => void;
}) {
  const [place, setPlace] = useState<{ top: number; right: number } | null>(null);
  const open = place !== null;

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setPlace(null);
      }
    };
    document.addEventListener('keydown', escape);
    return () => document.removeEventListener('keydown', escape);
  }, [open]);

  const pick = (action: () => void) => () => {
    setPlace(null);
    action();
  };
  const label = `Действия: ${person.username}`;
  return (
    <div className="row-menu">
      <button
        type="button"
        className="icon-button row-menu-button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          setPlace(open ? null : { top: box.bottom + 4, right: window.innerWidth - box.right });
        }}
      >
        ⋯
      </button>
      {open && (
        <>
          <div className="menu-scrim" aria-hidden="true" onClick={() => setPlace(null)} />
          <div className="row-menu-list" role="menu" aria-label={label} style={place}>
            <button type="button" role="menuitem" onClick={pick(() => onPick('roles'))}>
              Изменить роли
            </button>
            {person.enabled ? (
              <>
                <button type="button" role="menuitem" onClick={pick(() => onPick('link'))}>
                  Новая ссылка для входа
                </button>
                {!self && (
                  <button type="button" role="menuitem" className="danger" onClick={pick(() => onPick('block'))}>
                    Заблокировать
                  </button>
                )}
              </>
            ) : (
              <button type="button" role="menuitem" onClick={pick(onUnblock)}>
                Разблокировать
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function RoleChoice({
  roles,
  onChange,
  lockAdmin = false,
}: {
  readonly roles: ReadonlySet<Role>;
  readonly onChange: (roles: ReadonlySet<Role>) => void;
  readonly lockAdmin?: boolean;
}) {
  const toggle = (role: Role) => {
    const next = new Set(roles);
    if (next.has(role)) {
      next.delete(role);
    } else {
      next.add(role);
    }
    onChange(next);
  };
  return (
    <fieldset className="option-cards">
      <legend>Роли</legend>
      {ROLES.map((role) => {
        const locked = lockAdmin && role === 'admin';
        return (
          <label key={role} className="option-card">
            <input type="checkbox" checked={roles.has(role)} disabled={locked} onChange={() => toggle(role)} />
            <span>
              {ROLE_LABELS[role]}
              <span className="hint">{locked ? 'Свою роль администратора снять нельзя' : ROLE_HINTS[role]}</span>
            </span>
          </label>
        );
      })}
    </fieldset>
  );
}

function TtlChoice({ value, onChange }: { readonly value: InviteTtl; readonly onChange: (v: InviteTtl) => void }) {
  return (
    <fieldset className="segmented-field">
      <legend>Ссылка действует</legend>
      <div className="segmented-choice">
        {TTLS.map((ttl) => (
          <label key={ttl}>
            <input type="radio" name="ttl" checked={value === ttl} onChange={() => onChange(ttl)} />
            <span>{TTL_LABELS[ttl]}</span>
          </label>
        ))}
      </div>
      <p className="hint">Ссылка одноразовая: по ней пользователь задаст пароль и сразу войдёт.</p>
    </fieldset>
  );
}

const USERNAME = /^[a-z0-9][a-z0-9._-]{1,63}$/;
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function CreateForm({
  api,
  onCreated,
}: {
  readonly api: IdentityApi;
  readonly onCreated: (username: string, invite: InviteLink) => void;
}) {
  const [username, setUsername] = useState('');
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [roles, setRoles] = useState<ReadonlySet<Role>>(new Set());
  const [ttl, setTtl] = useState<InviteTtl>(7);
  const { run, error, busy } = useAction(() => undefined);

  const login = username.trim().toLowerCase();
  const loginProblem = login !== '' && !USERNAME.test(login);
  const emailProblem = email.trim() !== '' && !EMAIL.test(email.trim());
  const ready = USERNAME.test(login) && firstName.trim() !== '' && lastName.trim() !== '' && !emailProblem;

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(async () => {
      const created = await api.create({
        username: login,
        first_name: firstName.trim(),
        last_name: lastName.trim(),
        email: email.trim() || null,
        roles: ROLES.filter((role) => roles.has(role)),
        ttl_days: ttl,
      });
      onCreated(created.user.username, created.invite);
    });
  }

  return (
    <form className="drawer-form" aria-label="Новый пользователь" onSubmit={submit}>
      <label>
        Логин
        <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="ivan.petrov" maxLength={64} />
      </label>
      {loginProblem ? (
        <p className="field-error">Латинские буквы, цифры, точка, дефис и подчёркивание; от 2 символов.</p>
      ) : (
        <p className="hint">Латинские буквы, цифры, точка, дефис и подчёркивание.</p>
      )}
      <label>
        Имя
        <input value={firstName} onChange={(e) => setFirstName(e.target.value)} maxLength={100} />
      </label>
      <label>
        Фамилия
        <input value={lastName} onChange={(e) => setLastName(e.target.value)} maxLength={100} />
      </label>
      <label>
        <span>
          Email <span className="optional">— необязательно</span>
        </span>
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={255} />
      </label>
      {emailProblem && <p className="field-error">Проверьте адрес: в нём должны быть @ и домен.</p>}
      <RoleChoice roles={roles} onChange={setRoles} />
      <TtlChoice value={ttl} onChange={setTtl} />
      <ErrorNote message={error} />
      <button type="submit" className="primary" disabled={busy || !ready}>
        Создать и получить ссылку
      </button>
    </form>
  );
}

function RolesForm({
  api,
  person,
  self,
  onDone,
}: {
  readonly api: IdentityApi;
  readonly person: Person;
  readonly self: boolean;
  readonly onDone: () => void;
}) {
  const [roles, setRoles] = useState<ReadonlySet<Role>>(new Set(person.roles));
  const { run, error, busy } = useAction(onDone);

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(() =>
      api.setRoles(
        person.id,
        ROLES.filter((role) => roles.has(role)),
      ),
    );
  }

  return (
    <form className="drawer-form" aria-label="Роли" onSubmit={submit}>
      <p className="hint">Новые роли действуют со следующего обновления входа — в течение нескольких минут.</p>
      <RoleChoice roles={roles} onChange={setRoles} lockAdmin={self} />
      <ErrorNote message={error} />
      <button type="submit" className="primary" disabled={busy}>
        Сохранить
      </button>
    </form>
  );
}

function LinkForm({
  api,
  person,
  onIssued,
}: {
  readonly api: IdentityApi;
  readonly person: Person;
  readonly onIssued: (invite: InviteLink) => void;
}) {
  const [ttl, setTtl] = useState<InviteTtl>(7);
  const { run, error, busy } = useAction(() => undefined);

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(async () => onIssued(await api.invite(person.id, ttl)));
  }

  return (
    <form className="drawer-form" aria-label="Новая ссылка" onSubmit={submit}>
      <p>
        Для <b>{nameOf(person)}</b> ({person.username}). По ссылке пользователь задаст новый пароль — так
        восстанавливают забытый. Прежние ссылки перестанут работать; текущий пароль действует, пока по ссылке не
        задан новый.
      </p>
      <TtlChoice value={ttl} onChange={setTtl} />
      <ErrorNote message={error} />
      <button type="submit" className="primary" disabled={busy}>
        Выдать ссылку
      </button>
    </form>
  );
}

function BlockForm({
  api,
  person,
  onDone,
}: {
  readonly api: IdentityApi;
  readonly person: Person;
  readonly onDone: () => void;
}) {
  const { run, error, busy } = useAction(onDone);
  return (
    <section className="drawer-form">
      <p>
        <b>{nameOf(person)}</b> ({person.username}) сразу выйдет из системы на всех устройствах, в том числе там, где
        отмечено «Запомнить меня». Войти снова сможет, когда вы разблокируете.
      </p>
      <ErrorNote message={error} />
      <button type="button" className="primary danger-button" disabled={busy} onClick={() => void run(() => api.setEnabled(person.id, false))}>
        Заблокировать
      </button>
    </section>
  );
}

function Issued({
  username,
  invite,
  created,
  onDone,
}: {
  readonly username: string;
  readonly invite: InviteLink;
  readonly created: boolean;
  readonly onDone: () => void;
}) {
  const url = inviteUrl(invite.token);
  const [copied, setCopied] = useState<boolean | null>(null);

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <section className="drawer-form issued">
      <p>
        {created ? (
          <>
            Пользователь <b>{username}</b> создан. Передайте ему ссылку
          </>
        ) : (
          <>
            Передайте ссылку пользователю <b>{username}</b>
          </>
        )}
        : по ней он задаст пароль и сразу войдёт. Ссылка одноразовая, действует до {when(invite.expires_at)}.
      </p>
      <div className="link-box">
        <input readOnly value={url} aria-label="Ссылка для входа" onFocus={(e) => e.target.select()} />
        <button type="button" className="primary" onClick={() => void copy()}>
          {copied ? 'Скопировано' : 'Скопировать'}
        </button>
      </div>
      {copied === false && (
        <p className="field-error">Браузер не дал скопировать — выделите ссылку и скопируйте вручную.</p>
      )}
      <p className="hint">
        Ссылку видно только сейчас: в системе хранится лишь её отпечаток. Потеряли — выдайте новую.
      </p>
      <button type="button" onClick={onDone}>
        Готово
      </button>
    </section>
  );
}
