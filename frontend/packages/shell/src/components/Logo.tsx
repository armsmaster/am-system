/** Знак AM System — тот же, что во вкладке браузера (assets/favicon.svg). */
export function Logo() {
  return (
    <svg className="brand-mark" viewBox="0 0 64 64" width={28} height={28} aria-hidden="true">
      <defs>
        <linearGradient id="am-logo-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#3b82f6" />
          <stop offset="1" stopColor="#1e3a8a" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="15" fill="url(#am-logo-bg)" />
      <g fill="#fff" fillOpacity={0.3}>
        <rect x="13" y="38" width="9" height="13" rx="2.5" />
        <rect x="27.5" y="31" width="9" height="20" rx="2.5" />
        <rect x="42" y="24" width="9" height="27" rx="2.5" />
      </g>
      <path
        d="M12 36 25.5 25l9 6.5L49 17"
        fill="none"
        stroke="#fff"
        strokeWidth={5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="49.5" cy="16.5" r="5" fill="#fff" />
    </svg>
  );
}
