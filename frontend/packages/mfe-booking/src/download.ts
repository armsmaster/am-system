/** Сохранить файл из ответа сервиса: браузер скачивает его под этим именем. */
export function saveFile(file: Blob, name: string): void {
  const url = URL.createObjectURL(file);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  // Ссылку отзываем не сразу: иначе часть браузеров не успевает начать скачивание.
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
