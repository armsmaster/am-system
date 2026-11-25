/** Правила пароля — те же, что проверяет сервис входа: не короче 10 символов, не логин. */
export const MIN_PASSWORD_LENGTH = 10;

export interface Rule {
  readonly label: string;
  readonly ok: boolean;
}

export function passwordRules(password: string, repeat: string, username: string): readonly Rule[] {
  return [
    { label: `Не короче ${MIN_PASSWORD_LENGTH} символов`, ok: password.length >= MIN_PASSWORD_LENGTH },
    {
      label: 'Не совпадает с логином',
      ok: password !== '' && password.toLowerCase() !== username.toLowerCase(),
    },
    { label: 'Пароли совпадают', ok: password !== '' && password === repeat },
  ];
}
