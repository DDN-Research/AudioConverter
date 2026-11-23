# Історія змін / Changelog

## 2.3 — 2026-10-22

- Колесо миші: гучність ±5 %, смуги еквалайзера ±1 дБ, таймлайн ±5 с.
  Mouse wheel: volume ±5%, equalizer bands ±1 dB, timeline ±5 s.
- Пакет з EXE (PyInstaller) з вбудованими Python і FFmpeg; значок програми.
  EXE package (PyInstaller) with bundled Python and FFmpeg; application icon.
- Двомовна документація (README, docs/index.html, інструкція до EXE).
  Bilingual documentation (README, docs/index.html, EXE instructions).
- Правильні відмінки в повідомленнях («2 частини», «5 частин»).
  Correct Ukrainian plural forms in messages.

## 2.2 — 2026-10-22

- Вбудований програвач: таймлайн із перемотуванням, гучність 0–200 %, 10-смуговий еквалайзер із профілями.
  Built-in player: seekable timeline, volume 0–200%, 10-band equalizer with presets.
- Позначки «Початок / Кінець ділянки» з поточної позиції програвача.
  Mark segment start/end from the current playback position.
- Еквалайзер можна застосувати під час перекодування.
  The equalizer can be applied when converting.

## 2.1 — 2026-10-22

- Своє зображення обкладинки завжди зменшується до 500×500 зі збереженням пропорцій.
  A custom cover image is always scaled down to 500×500, keeping proportions.

## 2.0 — 2026-10-22

- Вкладка «Теги й обкладинка»: редагування основних тегів, своя обкладинка.
  “Tags & cover” tab: edit main tags, custom cover art.
- Вкладка «Розбиття на ділянки»: автоматично, вручну або за треклістом.
  “Splitting” tab: automatic, manual or from a track list.
- Галочки «Лише ID3v2.3» та «ID3v1 у Windows-1251».
  “ID3v2.3 only” and “ID3v1 in Windows-1251” options.

## 1.0 — 2026-10

- Аналіз файлів і перевірка сумісності з автомагнітолою, перекодування через FFmpeg,
  профіль «Автомагнітола», копіювання параметрів із файлу, що грає, розбиття по N хвилин.
  File analysis and car-stereo compatibility check, FFmpeg conversion, “Car stereo”
  profile, copying settings from a known-good file, splitting every N minutes.
