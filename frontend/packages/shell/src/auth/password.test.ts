import { describe, expect, it } from 'vitest';

import { passwordRules } from './password';

const ok = (password: string, repeat: string, username = 'ivanov') =>
  passwordRules(password, repeat, username).map((rule) => rule.ok);

describe('passwordRules', () => {
  it('checks length, login and repeat', () => {
    expect(ok('', '')).toEqual([false, false, false]);
    expect(ok('short', 'short')).toEqual([false, true, true]);
    expect(ok('long-enough-1', 'long-enough-2')).toEqual([true, true, false]);
    expect(ok('Ivan.Petrov', 'Ivan.Petrov', 'ivan.petrov')).toEqual([true, false, true]);
    expect(ok('long-enough-1', 'long-enough-1')).toEqual([true, true, true]);
  });
});
