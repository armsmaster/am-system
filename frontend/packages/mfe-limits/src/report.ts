import type { Report } from './api';
import {
  breachLimit,
  breachValue,
  formatDate,
  KIND_LABELS,
  monthText,
  STATE_LABELS,
  STATUS_LABELS,
  thresholdText,
  usageText,
  valueText,
} from './format';

/**
 * Отчёт за месяц для Excel (п. 8.8 политики): CSV с разделителем «;» и BOM —
 * так Excel с русскими настройками сам разложит столбцы и узнает UTF-8.
 */
export function reportCsv(report: Report): string {
  const { day } = report;
  const usage = !day.limit_set
    ? [['Набора лимитов на этот день нет.']]
    : !day.calculated
      ? [['Расчёта фонда за этот день нет.']]
      : [
          ['Лимит', 'Проверка', 'Значение', 'Порог', 'Использовано', 'Статус'],
          ...day.checks.map((c) => [
            `${c.code} · ${c.name}`,
            c.label ?? '',
            valueText(c),
            thresholdText(c),
            usageText(c.usage),
            STATUS_LABELS[c.status],
          ]),
        ];
  const breaches = report.breaches.length
    ? [
        [
          'Лимит',
          'Группа',
          'Значение',
          'День нарушения',
          'Выявлено',
          'Вид',
          'Срок',
          'Статус',
          'Устранено',
          'Продления',
          'Комментарии',
        ],
        ...report.breaches.map((b) => [
          breachLimit(b),
          b.name,
          breachValue(b),
          formatDate(b.first_day),
          formatDate(b.detected_on),
          KIND_LABELS[b.kind],
          formatDate(b.deadline),
          STATE_LABELS[b.state],
          b.eliminated_on ? formatDate(b.eliminated_on) : '',
          b.extensions.map((e) => `до ${formatDate(e.deadline)}, ${e.decision}`).join(' | '),
          b.notes.map((n) => `${n.by}: ${n.text}`).join(' | '),
        ]),
      ]
    : [['Нарушений за месяц не было.']];
  const rows = [
    [`Использование лимитов: ${day.fund.code} — ${day.fund.name}, ${monthText(report.month)}`],
    [],
    [`Использование на ${formatDate(day.date)}`],
    ...usage,
    [],
    ['Нарушения за месяц'],
    ...breaches,
  ];
  return `﻿${rows.map((row) => row.map(cell).join(';')).join('\r\n')}\r\n`;
}

function cell(value: string): string {
  return /[";\r\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

/** Сохраняет текст файлом: браузер предложит его скачать. */
export function download(name: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}
