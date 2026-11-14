import type { ReactElement } from 'react';

/** Контурные значки справочника — в одном стиле со значками оболочки. */
const PATHS = {
  pencil: (
    <>
      <path d="M4 20h4L18.5 9.5a2.83 2.83 0 0 0-4-4L4 16v4" />
      <path d="M13.5 6.5l4 4" />
    </>
  ),
  check: <path d="M5 12l5 5L20 7" />,
  x: <path d="M18 6 6 18M6 6l12 12" />,
  plus: <path d="M12 5v14M5 12h14" />,
  file: (
    <>
      <path d="M14 3v4a1 1 0 0 0 1 1h4" />
      <path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2z" />
      <path d="M9 13h6M9 17h4" />
    </>
  ),
  building: (
    <>
      <path d="M3 21h18" />
      <path d="M5 21V7l8-4v18" />
      <path d="M19 21V11l-6-4" />
      <path d="M9 9v.01M9 12v.01M9 15v.01M9 18v.01" />
    </>
  ),
} satisfies Record<string, ReactElement>;

export type GlyphName = keyof typeof PATHS;

export function Glyph({ name, size = 16 }: { readonly name: GlyphName; readonly size?: number }) {
  return (
    <svg
      className="glyph"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {PATHS[name]}
    </svg>
  );
}
