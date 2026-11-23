# -*- coding: utf-8 -*-
"""
Аудіоконвертер для автомагнітоли.

Показує параметри вхідного аудіофайлу, перевіряє його сумісність
з типовими автомагнітолами та перекодовує в обраний формат
(бітрейт, частота дискретизації, канали, теги тощо).

Потрібно: Python 3.8+ (з tkinter — стандартно у Windows) та FFmpeg
(ffmpeg.exe і ffprobe.exe у PATH, у папці програми або у підпапці ffmpeg\\bin).
"""

import copy
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

APP_NAME = "Аудіоконвертер для автомагнітоли"
APP_VERSION = "2.3"

AUDIO_EXT = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus",
             ".wma", ".ape", ".wv", ".aiff", ".aif", ".mka", ".ac3", ".dts",
             ".mp2", ".amr", ".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4b")

# --------------------------------------------------------------------------
#  Вихідні формати
# --------------------------------------------------------------------------
FORMATS = {
    "MP3": {"ext": ".mp3", "codec": "libmp3lame", "lossless": False,
            "bitrates": ["32", "64", "96", "128", "160", "192", "224", "256", "320"],
            "default_br": "256", "vbr": True,
            "rates": ["44100", "48000", "32000", "22050", "16000"]},
    "AAC (M4A)": {"ext": ".m4a", "codec": "aac", "lossless": False,
                  "bitrates": ["64", "96", "128", "160", "192", "256", "320"],
                  "default_br": "192", "vbr": False,
                  "rates": ["44100", "48000", "32000", "22050"]},
    "WMA": {"ext": ".wma", "codec": "wmav2", "lossless": False,
            "bitrates": ["64", "96", "128", "160", "192", "256", "320"],
            "default_br": "192", "vbr": False,
            "rates": ["44100", "48000", "32000", "22050"]},
    "OGG Vorbis": {"ext": ".ogg", "codec": "libvorbis", "lossless": False,
                   "bitrates": ["64", "96", "128", "160", "192", "256", "320"],
                   "default_br": "192", "vbr": False,
                   "rates": ["44100", "48000", "32000", "22050"]},
    "FLAC": {"ext": ".flac", "codec": "flac", "lossless": True,
             "bitrates": [], "default_br": "", "vbr": False,
             "rates": ["44100", "48000", "88200", "96000", "32000", "22050"]},
    "WAV (PCM)": {"ext": ".wav", "codec": "pcm", "lossless": True,
                  "bitrates": [], "default_br": "", "vbr": False,
                  "rates": ["44100", "48000", "88200", "96000", "32000", "22050"]},
}

# Якість LAME VBR: V0 (найкраща, ~245 кбіт/с) … V9 (~65 кбіт/с)
MP3_VBR_LEVELS = ["V0 (~245 кбіт/с)", "V1 (~225)", "V2 (~190)", "V3 (~175)",
                  "V4 (~165)", "V5 (~130)", "V6 (~115)", "V7 (~100)",
                  "V8 (~85)", "V9 (~65)"]

CAR_PRESET = {
    "format": "MP3", "mode": "CBR (постійний)", "bitrate": "256",
    "vbr_level": MP3_VBR_LEVELS[2], "rate": "44100", "channels": "Стерео",
    "bits": "16", "id3": "ID3v2.3 + ID3v1", "keep_tags": True,
    "keep_cover": False, "normalize": False, "split": "0",
    # нове у версії 2.0
    "id3_only23": True,      # теги лише ID3v2.3 (без ID3v2.4)
    "id3v1_cp1251": True,    # ID3v1 у кодуванні Windows-1251 (кирилиця на старих магнітолах)
    "cover_resize": True,    # обкладинку — JPEG не більше 500×500
    "split_folder": False,   # частини одного файлу — в окрему папку
}

# Основні теги, які можна редагувати: (ключ FFmpeg, підпис)
TAG_FIELDS = [("title", "Назва"), ("artist", "Виконавець"), ("album", "Альбом"),
              ("album_artist", "Виконавець альбому"), ("date", "Рік"),
              ("genre", "Жанр"), ("track", "№ треку")]
GENRES = ["Pop", "Rock", "Dance", "Electronic", "Hip-Hop", "R&B", "Jazz", "Blues",
          "Classical", "Folk", "Country", "Metal", "Punk", "Reggae", "Chanson",
          "Soundtrack", "Audiobook", "Podcast", "Other"]
COVER_MAX = 500

# Еквалайзер: 10 смуг (Гц) і готові налаштування (дБ)
EQ_BANDS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
EQ_PRESETS = {
    "Плоский (без змін)": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    "Більше басів": [6, 5, 4, 2, 0, 0, 0, 0, 0, 0],
    "Менше басів (малі колонки)": [-6, -5, -3, -1, 0, 0, 0, 0, 0, 0],
    "Рок": [5, 4, 3, 1, -1, -1, 1, 3, 4, 5],
    "Поп": [-1, 1, 3, 4, 3, 0, -1, -1, 0, 1],
    "Танцювальна": [6, 5, 2, 0, 0, -2, -1, 0, 3, 4],
    "Джаз": [3, 2, 1, 2, -1, -1, 0, 1, 2, 3],
    "Класика": [4, 3, 2, 1, 0, 0, 0, 1, 2, 3],
    "Голос / аудіокнига": [-4, -3, -1, 1, 3, 4, 4, 2, 0, -2],
    "Більше високих": [0, 0, 0, 0, 0, 1, 2, 4, 5, 6],
}


def eq_filters(gains, sample_rate=44100):
    """Ланцюжок фільтрів FFmpeg для еквалайзера (порожній, якщо всі смуги на нулі)."""
    out = []
    nyq = (sample_rate or 44100) / 2.0
    for f, g in zip(EQ_BANDS, gains or []):
        if abs(g) >= 0.1 and f < nyq * 0.9:
            out.append("equalizer=f=%d:t=o:w=1:g=%.1f" % (f, g))
    if out and max(gains) > 0:
        out.append("alimiter=limit=0.95:level=disabled")  # щоб підсилення не «хрипіло»
    return out

DEFAULT_SETTINGS = dict(CAR_PRESET)
DEFAULT_SETTINGS.update({"eq_apply": False, "eq_gains": [0] * len(EQ_BANDS),
                         "eq_preset": "Плоский (без змін)", "volume": 100})


# --------------------------------------------------------------------------
#  Пошук FFmpeg
# --------------------------------------------------------------------------
def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def config_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "AudioConverterCar")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "settings.json")


def load_config():
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def find_tool(name, hint_dir=None):
    exe = name + (".exe" if os.name == "nt" else "")
    candidates = []
    if hint_dir:
        candidates += [os.path.join(hint_dir, exe), os.path.join(hint_dir, "bin", exe)]
    d = app_dir()
    candidates += [os.path.join(d, exe), os.path.join(d, "ffmpeg", "bin", exe),
                   os.path.join(d, "ffmpeg", exe)]
    for c in candidates:
        if os.path.isfile(c):
            return c
    found = shutil.which(name)
    if found:
        return found
    # Типові місця встановлення у Windows (winget / вручну)
    if os.name == "nt":
        roots = [os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", ""),
                 "C:\\ffmpeg"]
        for root in roots:
            if not root or not os.path.isdir(root):
                continue
            if root.lower().endswith("ffmpeg"):
                for c in (os.path.join(root, "bin", exe), os.path.join(root, exe)):
                    if os.path.isfile(c):
                        return c
                continue
            pk = os.path.join(root, "Microsoft", "WinGet", "Packages")
            if os.path.isdir(pk):
                for dirpath, _dirs, files in os.walk(pk):
                    if exe in files and dirpath.lower().endswith("bin"):
                        return os.path.join(dirpath, exe)
    return None


def _popen_kwargs():
    # stdin=DEVNULL — щоб FFmpeg запускався і з EXE без консолі (PyInstaller --windowed)
    kw = {"stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW — без чорного вікна консолі
    return kw


# --------------------------------------------------------------------------
#  Аналіз вхідного файлу
# --------------------------------------------------------------------------
_MP3_BR = {  # (версія MPEG, шар) -> таблиця бітрейтів
    (1, 3): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    (2, 3): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}


def _synchsafe(b):
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def mp3_header_info(path):
    """Читає ID3-теги та перший кадр MP3: версію ID3, тип бітрейту, кодувальник."""
    res = {}
    try:
        with open(path, "rb") as f:
            head = f.read(512 * 1024)
            f.seek(0, 2)
            size = f.tell()
            if size > 128:
                f.seek(-128, 2)
                res["id3v1"] = f.read(3) == b"TAG"
    except OSError:
        return res
    pos = 0
    if head[:3] == b"ID3" and len(head) >= 10:
        res["id3v2"] = "ID3v2.%d" % head[3]
        res["id3v2_size"] = _synchsafe(head[6:10]) + 10
        pos = res["id3v2_size"]
        if pos > len(head):
            try:
                with open(path, "rb") as f:
                    f.seek(pos)
                    head = b"\0" * pos + f.read(64 * 1024)
            except OSError:
                return res
    # пошук першого кадру
    limit = min(len(head) - 4, pos + 64 * 1024)
    while pos < limit:
        if head[pos] == 0xFF and (head[pos + 1] & 0xE0) == 0xE0:
            break
        pos += 1
    else:
        return res
    hdr = int.from_bytes(head[pos:pos + 4], "big")
    ver_bits = (hdr >> 19) & 3
    mode = (hdr >> 6) & 3
    res["mpeg"] = {3: "MPEG-1", 2: "MPEG-2", 0: "MPEG-2.5"}.get(ver_bits, "?")
    res["layer"] = {1: "Layer III", 2: "Layer II", 3: "Layer I"}.get((hdr >> 17) & 3, "?")
    res["stereo_mode"] = ["Стерео", "Joint Stereo", "Dual Channel", "Моно"][mode]
    frame = head[pos:pos + 400]
    if b"Xing" in frame:
        res["brmode"] = "VBR (змінний)"
    elif b"Info" in frame:
        res["brmode"] = "CBR (постійний)"
    elif b"VBRI" in frame:
        res["brmode"] = "VBR (змінний, VBRI)"
    else:
        # немає службового заголовка — перевіримо бітрейти перших кадрів
        res["brmode"] = _guess_brmode(head, pos)
        res["no_vbr_header"] = res["brmode"].startswith("VBR")
    if b"Lavc" in frame or b"Lavf" in frame:
        res["encoder"] = "FFmpeg (libmp3lame)"
    elif b"LAME" in frame:
        i = frame.find(b"LAME")
        res["encoder"] = frame[i:i + 9].split(b"\0")[0].decode("latin1", "replace").strip()
    return res


def _guess_brmode(buf, pos):
    seen = set()
    n = 0
    while pos < len(buf) - 4 and n < 200:
        h = int.from_bytes(buf[pos:pos + 4], "big")
        if (h >> 21) & 0x7FF != 0x7FF:
            break
        vb = (h >> 19) & 3
        ver = 1 if vb == 3 else 2
        bri = (h >> 12) & 15
        sri = (h >> 10) & 3
        pad = (h >> 9) & 1
        rates = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}
        if bri in (0, 15) or sri == 3 or vb == 1:
            break
        br = _MP3_BR[(ver, 3)][bri] * 1000
        sr = rates[vb][sri]
        seen.add(br)
        flen = (144 if ver == 1 else 72) * br // sr + pad
        if flen <= 0:
            break
        pos += flen
        n += 1
    if len(seen) > 1:
        return "VBR (змінний, без заголовка Xing)"
    return "CBR (постійний)" if seen else "невідомо"


def probe_file(ffprobe, path):
    """Повертає словник з параметрами файлу."""
    cmd = [ffprobe, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", path]
    p = subprocess.run(cmd, capture_output=True, **_popen_kwargs())
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "replace").strip() or "ffprobe: помилка")
    data = json.loads(p.stdout.decode("utf-8", "replace"))
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    cover = [s for s in streams if s.get("codec_type") == "video"]
    info = {
        "path": path,
        "size": os.path.getsize(path),
        "container": fmt.get("format_long_name") or fmt.get("format_name", "?"),
        "format_name": fmt.get("format_name", ""),
        "duration": float(fmt.get("duration") or 0),
        "overall_bitrate": int(fmt.get("bit_rate") or 0),
        "tags": {k.lower(): v for k, v in (fmt.get("tags") or {}).items()},
        "audio_streams": sum(1 for s in streams if s.get("codec_type") == "audio"),
        "cover": bool(cover),
        "cover_size": "",
    }
    if cover:
        c = cover[0]
        info["cover_size"] = "%sx%s" % (c.get("width", "?"), c.get("height", "?"))
    if audio:
        info.update({
            "codec": audio.get("codec_name", "?"),
            "codec_long": audio.get("codec_long_name", ""),
            "profile": audio.get("profile", ""),
            "sample_rate": int(audio.get("sample_rate") or 0),
            "channels": int(audio.get("channels") or 0),
            "layout": audio.get("channel_layout", ""),
            "bitrate": int(audio.get("bit_rate") or 0),
            "sample_fmt": audio.get("sample_fmt", ""),
            "bits": int(audio.get("bits_per_raw_sample") or audio.get("bits_per_sample") or 0),
        })
        info["tags"].update({k.lower(): v for k, v in (audio.get("tags") or {}).items()
                             if k.lower() not in info["tags"]})
    else:
        info["codec"] = None
    if info.get("codec") == "mp3":
        info["mp3"] = mp3_header_info(path)
    return info


def plural(n, one, few, many):
    """Українські відмінки після числа: 1 частина, 2 частини, 5 частин."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "%d %s" % (n, one)
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "%d %s" % (n, few)
    return "%d %s" % (n, many)


def fmt_size(n):
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return ("%.0f %s" if unit == "Б" else "%.1f %s") % (n, unit)
        n /= 1024.0


def fmt_time(sec):
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


# --------------------------------------------------------------------------
#  Час, ділянки, трекліст
# --------------------------------------------------------------------------
def parse_time(text):
    """«1:02:03», «62:03», «3723», «3:45,5» → секунди (float). Порожньо → None."""
    text = (text or "").strip()
    if not text:
        return None
    parts = text.replace(",", ".").split(":")
    if len(parts) > 3:
        raise ValueError("Неправильний час: «%s»" % text)
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise ValueError("Неправильний час: «%s». Приклади: 1:02:03, 45:10, 90" % text)
    if any(n < 0 for n in nums) or any(n >= 60 for n in nums[1:]):
        raise ValueError("Неправильний час: «%s» (хвилини й секунди — до 59)" % text)
    sec = 0.0
    for n in nums:
        sec = sec * 60 + n
    return sec


def fmt_ts(sec):
    """Секунди → «1:02:03» або «3:45» (з десятими, якщо є)."""
    if sec is None:
        return ""
    tenths = int(round(sec * 10))
    whole, frac = divmod(tenths, 10)
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    txt = "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)
    return txt + (".%d" % frac if frac else "")


_TS_IN_LINE = re.compile(r"(?<![\d:])((?:\d{1,2}:)?\d{1,3}:\d{2}(?:[.,]\d+)?)(?![\d:])")


def parse_tracklist(text, duration=0):
    """Трекліст («0:00 Пісня», «1. Виконавець - Пісня 3:45», «[1:02:03] Пісня»)
    → список ділянок [початок, кінець, назва]. Кінець = початок наступної."""
    starts = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _TS_IN_LINE.search(line)
        if not m:
            continue
        try:
            t = parse_time(m.group(1))
        except ValueError:
            continue
        name = (line[:m.start()] + " " + line[m.end():]).strip()
        name = re.sub(r"[\(\[]\s*[\)\]]", "", name)            # порожні дужки від часу
        name = re.sub(r"^\d{1,3}\s*[.)]\s+", "", name)          # номер «1.» / «01)»
        name = name.strip(" \t-–—|:•·")
        name = re.sub(r"\s{2,}", " ", name)
        starts.append((t, name))
    starts.sort(key=lambda x: x[0])
    if duration:
        starts = [x for x in starts if x[0] < duration]
    segs = []
    for i, (t, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else None
        if end is not None and end <= t:
            continue
        segs.append([t, end, name])
    return segs


def auto_segments(duration, minutes):
    """Ділянки по N хвилин (останній короткий хвіст до 30 с приєднується)."""
    if minutes <= 0 or duration <= 0:
        return []
    step = minutes * 60
    if duration <= step + 30:
        return [[0.0, None, ""]]
    parts = int(duration // step) + (1 if duration % step > 30 else 0)
    return [[i * step, None if i == parts - 1 else (i + 1) * step, ""] for i in range(parts)]


def equal_segments(duration, count):
    if count < 1 or duration <= 0:
        return []
    step = duration / count
    return [[round(i * step, 1), None if i == count - 1 else round((i + 1) * step, 1), ""]
            for i in range(count)]


def safe_name(text, limit=80):
    """Ім'я файлу без заборонених у Windows символів."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text or "").strip(" .")
    return text[:limit].strip(" .") or "без назви"


def source_tags(info):
    """Основні теги вхідного файлу як словник {ключ: текст}."""
    t = info.get("tags", {})
    alt = {"date": ("year", "tyer", "tdrc"), "album_artist": ("album artist", "albumartist", "tpe2"),
           "track": ("tracknumber", "trck")}
    out = {}
    for k, _label in TAG_FIELDS:
        val = t.get(k, "")
        for a in alt.get(k, ()):
            if not val:
                val = t.get(a, "")
        out[k] = str(val).strip()
    return out


def new_edits(info):
    """Початкові налаштування окремого файлу: теги, обкладинка, розбиття."""
    tags = source_tags(info)
    return {"orig": dict(tags), "tags": dict(tags),
            "cover": {"mode": "global", "path": ""},
            "split": {"mode": "global", "segs": [], "artist_title": False}}


def read_out_tags(ffprobe, path):
    try:
        p = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", path],
                           capture_output=True, **_popen_kwargs())
        tags = json.loads(p.stdout.decode("utf-8", "replace")).get("format", {}).get("tags", {})
        return {k.lower(): v for k, v in tags.items()}
    except Exception:  # noqa: BLE001
        return {}


def write_id3v1_cp1251(path, tags):
    """Переписує блок ID3v1 (останні 128 байт) у кодуванні Windows-1251.
    FFmpeg записує туди UTF-8, і старі магнітоли показують «кракозябри»."""
    def enc(val, n):
        raw = (val or "").encode("cp1251", "replace")[:n]
        return raw + b"\0" * (n - len(raw))
    try:
        with open(path, "r+b") as f:
            f.seek(-128, 2)
            blk = bytearray(f.read(128))
            if blk[:3] != b"TAG":
                return False
            blk[3:33] = enc(tags.get("title"), 30)
            blk[33:63] = enc(tags.get("artist"), 30)
            blk[63:93] = enc(tags.get("album"), 30)
            year = re.search(r"\d{4}", tags.get("date", "") or "")
            blk[93:97] = enc(year.group(0) if year else "", 4)
            f.seek(-128, 2)
            f.write(bytes(blk))
        return True
    except OSError:
        return False


def fit_cover(w, h, limit=COVER_MAX):
    """Пропорційне зменшення до limit×limit (менші зображення не змінюються)."""
    if w <= limit and h <= limit:
        return w, h
    k = min(limit / float(w), limit / float(h))
    return max(1, int(w * k)), max(1, int(h * k))


def make_thumb(ffmpeg, src, dst, from_audio, size=150):
    """Мініатюра обкладинки у PNG (tkinter показує PNG без сторонніх бібліотек)."""
    cmd = [ffmpeg, "-v", "error", "-y", "-i", src]
    if from_audio:
        cmd += ["-map", "0:v:0"]
    cmd += ["-frames:v", "1", "-vf",
            "scale=%d:%d:force_original_aspect_ratio=decrease" % (size, size), dst]
    p = subprocess.run(cmd, capture_output=True, **_popen_kwargs())
    return p.returncode == 0 and os.path.isfile(dst)


def image_size(ffprobe, path):
    try:
        p = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0:s=x", path],
                           capture_output=True, **_popen_kwargs())
        return p.stdout.decode().strip().splitlines()[0]
    except Exception:  # noqa: BLE001
        return ""


def describe(info):
    """Список рядків (назва, значення) для таблиці параметрів."""
    rows = [("Файл", os.path.basename(info["path"])),
            ("Розмір", fmt_size(info["size"])),
            ("Тривалість", fmt_time(info["duration"])),
            ("Контейнер", info["container"])]
    if not info.get("codec"):
        rows.append(("Аудіо", "аудіодоріжку не знайдено"))
        return rows
    codec = info["codec"].upper()
    if info.get("profile") and info["profile"] != "unknown":
        codec += " (%s)" % info["profile"]
    rows.append(("Кодек", codec))
    mp3 = info.get("mp3", {})
    if mp3.get("mpeg"):
        rows.append(("Версія MPEG", "%s %s" % (mp3["mpeg"], mp3.get("layer", ""))))
    rows.append(("Частота дискретизації", "%d Гц" % info["sample_rate"] if info["sample_rate"] else "?"))
    ch = info["channels"]
    ch_txt = {1: "1 (моно)", 2: "2 (стерео)"}.get(ch, str(ch))
    if info.get("layout") and ch > 2:
        ch_txt += " — " + info["layout"]
    rows.append(("Канали", ch_txt))
    if mp3.get("stereo_mode"):
        rows.append(("Стереорежим", mp3["stereo_mode"]))
    br = info.get("bitrate") or info.get("overall_bitrate")
    rows.append(("Бітрейт (середній)", "%d кбіт/с" % round(br / 1000) if br else "—"))
    if mp3.get("brmode"):
        rows.append(("Тип бітрейту", mp3["brmode"]))
    if info.get("bits"):
        rows.append(("Розрядність", "%d біт" % info["bits"]))
    if mp3.get("encoder"):
        rows.append(("Кодувальник", mp3["encoder"]))
    elif info["tags"].get("encoder"):
        rows.append(("Кодувальник", info["tags"]["encoder"]))
    tag_ver = []
    if mp3.get("id3v2"):
        tag_ver.append(mp3["id3v2"])
    if mp3.get("id3v1"):
        tag_ver.append("ID3v1")
    if tag_ver:
        rows.append(("Версія тегів", " + ".join(tag_ver)))
    t = info["tags"]
    rows.append(("Виконавець", t.get("artist", "—")))
    rows.append(("Назва", t.get("title", "—")))
    rows.append(("Альбом", t.get("album", "—")))
    rows.append(("Обкладинка", ("так, " + info["cover_size"]) if info["cover"] else "немає"))
    return rows


def compat_issues(info, edits=None, s=None):
    """Перевірка під типову автомагнітолу. Повертає список (рівень, текст).
    edits — налаштування цього файлу (теги, розбиття), s — загальні налаштування."""
    out = []
    edits = edits or {}
    s = s or {}
    if not info.get("codec"):
        return [("error", "У файлі немає аудіо.")]
    codec = info["codec"]
    if codec not in ("mp3", "wmav2", "aac"):
        out.append(("error", "Кодек %s більшість автомагнітол не підтримує "
                             "(надійно — MP3, часто — WMA, рідше — AAC)." % codec.upper()))
    elif codec == "aac":
        out.append(("warn", "AAC/M4A підтримують не всі автомагнітоли. MP3 — найнадійніше."))
    if info["sample_rate"] and info["sample_rate"] != 44100:
        out.append(("error", "Частота %d Гц. Багато магнітол відтворюють лише 44 100 Гц."
                    % info["sample_rate"]))
    br = info.get("bitrate") or info.get("overall_bitrate")
    if br and br > 320000 and codec in ("mp3", "wmav2", "aac"):
        out.append(("error", "Бітрейт понад 320 кбіт/с — перевищує максимум для MP3."))
    mp3 = info.get("mp3", {})
    if mp3.get("brmode", "").startswith("VBR"):
        out.append(("warn", "Змінний бітрейт (VBR). Старі магнітоли можуть не грати або "
                            "неправильно показувати час — краще CBR."))
    if mp3.get("no_vbr_header"):
        out.append(("warn", "VBR без заголовка Xing: тривалість і перемотка можуть працювати неправильно."))
    if mp3.get("mpeg") and mp3["mpeg"] != "MPEG-1":
        out.append(("warn", "%s (низька частота) — частина магнітол не підтримує." % mp3["mpeg"]))
    if mp3.get("id3v2") == "ID3v2.4":
        if s.get("id3_only23", True):
            out.append(("info", "Теги ID3v2.4: деякі магнітоли показують назви лише з ID3v2.3. "
                                "Увімкнено «Лише ID3v2.3» — у результаті буде ID3v2.3."))
        else:
            out.append(("info", "Теги ID3v2.4: деякі магнітоли показують назви лише з ID3v2.3 — "
                                "поставте галочку «Лише ID3v2.3» праворуч."))
    if info["channels"] > 2:
        out.append(("error", "Багатоканальний звук (%d каналів) — потрібне стерео." % info["channels"]))
    if info["cover"]:
        out.append(("info", "Є вбудована обкладинка (%s). Великі обкладинки інколи "
                            "заважають відтворенню." % info["cover_size"]))
    split = edits.get("split") or {}
    mode = split.get("mode", "global")
    try:
        g_min = float(str(s.get("split", "0")).replace(",", "."))
    except ValueError:
        g_min = 0
    if mode == "manual" and split.get("segs"):
        split_txt = "буде розбито вручну на " + plural(len(split["segs"]), "ділянку", "ділянки", "ділянок")
    elif mode == "global" and g_min > 0 and info["duration"] > g_min * 60 + 30:
        split_txt = "буде розбито автоматично по %g хв" % g_min
    else:
        split_txt = ""
    if info["duration"] > 3600:
        if split_txt:
            out.append(("info", "Дуже довгий файл (%s) — %s." % (fmt_time(info["duration"]), split_txt)))
        else:
            out.append(("warn", "Дуже довгий файл (%s). Деякі магнітоли не відтворюють такі "
                                "файли — розбийте на частини (поле «Розбити на частини по, хв» "
                                "або вкладка «Розбиття на ділянки»)." % fmt_time(info["duration"])))
    elif split_txt:
        out.append(("info", "Файл %s." % split_txt))
    if info["size"] > 100 * 1024 * 1024:
        out.append(("warn", "Великий розмір (%s)." % fmt_size(info["size"])))
    tags = edits.get("tags") or {}
    if tags.get("title") or tags.get("artist"):
        if tags != edits.get("orig"):
            out.append(("info", "Теги змінено: буде записано «%s — %s»."
                        % (tags.get("artist") or "?", tags.get("title") or "?")))
    elif mode == "manual" and split.get("segs"):
        out.append(("info", "Назви частин візьмуться з назв ділянок."))
    else:
        out.append(("info", "Немає тегів «Назва»/«Виконавець» — на екрані буде лише ім'я файлу. "
                            "Заповніть їх на вкладці «Теги й обкладинка»."))
    cover = edits.get("cover") or {}
    if cover.get("mode") == "file" and cover.get("path"):
        out.append(("info", "Буде додано обкладинку: %s." % os.path.basename(cover["path"])))
    if not out:
        out.append(("ok", "Параметри типові для автомагнітол — файл має відтворюватися."))
    return out


# --------------------------------------------------------------------------
#  Побудова команди FFmpeg
# --------------------------------------------------------------------------
def part_tags(src, info, edits, part=None, parts=None, name=None):
    """Теги, які треба дописати поверх тегів файлу: змінені користувачем + назви частин."""
    edits = edits or {}
    tags = edits.get("tags") or source_tags(info)
    orig = edits.get("orig") or source_tags(info)
    meta = {k: v for k, v in tags.items() if v != orig.get(k, "")}
    is_part = (part is not None and parts and parts > 1) or bool(name)
    if is_part:
        base_title = tags.get("title") or os.path.splitext(os.path.basename(src))[0]
        if name:
            title = name
            split = edits.get("split") or {}
            if split.get("artist_title") and " - " in name:
                artist, title = [x.strip() for x in name.split(" - ", 1)]
                if artist:
                    meta["artist"] = artist
            meta["title"] = title
        else:
            meta["title"] = "%s (частина %d з %d)" % (base_title, part, parts)
        if parts and parts > 1:
            meta["track"] = "%d/%d" % (part, parts)
        if not tags.get("album"):
            meta["album"] = base_title  # щоб магнітола показувала частини як один альбом
    return meta


def build_command(ffmpeg, src, dst, s, info, start=None, length=None, part=None, parts=None,
                  name=None, edits=None):
    fmt = FORMATS[s["format"]]
    edits = edits or {}
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y"]
    if start is not None:
        cmd += ["-ss", "%.3f" % start]
    cmd += ["-i", src]

    # Обкладинка: з файлу (за галочкою або вибором для файлу), своє зображення або без неї
    cover_map = None
    encode_cover = False
    if fmt["ext"] in (".mp3", ".m4a", ".flac"):
        cv = edits.get("cover") or {}
        cmode = cv.get("mode", "global")
        if cmode == "file" and cv.get("path") and os.path.isfile(cv["path"]):
            cmd += ["-i", cv["path"]]
            cover_map, encode_cover = "1:v:0", True
        elif info.get("cover") and (cmode == "source" or (cmode == "global" and s["keep_cover"])):
            cover_map, encode_cover = "0:v:0", bool(s.get("cover_resize"))
    cmd += ["-map", "0:a:0"]
    if cover_map:
        cmd += ["-map", cover_map]
        if encode_cover:
            flt = "format=yuvj420p"
            # своє зображення зменшується завжди; обкладинка з файлу — за галочкою
            if s.get("cover_resize") or cover_map == "1:v:0":
                flt = ("scale='min(%d,iw)':'min(%d,ih)':force_original_aspect_ratio=decrease,"
                       % (COVER_MAX, COVER_MAX)) + flt
            cmd += ["-c:v", "mjpeg", "-filter:v", flt, "-q:v", "3"]
        else:
            cmd += ["-c:v", "copy"]
        cmd += ["-disposition:v:0", "attached_pic",
                "-metadata:s:v:0", "title=Album cover", "-metadata:s:v:0", "comment=Cover (front)"]
    else:
        cmd += ["-vn"]

    # Теги: теги файлу (за галочкою) + змінені користувачем + назви частин
    if s["keep_tags"]:
        cmd += ["-map_metadata", "0"]
    else:
        cmd += ["-map_metadata", "-1"]
    for k, val in part_tags(src, info, edits, part, parts, name).items():
        cmd += ["-metadata", "%s=%s" % (k, val)]

    # Кодек
    codec = fmt["codec"]
    if codec == "pcm":
        codec = "pcm_s24le" if s["bits"] == "24" else "pcm_s16le"
    cmd += ["-c:a", codec]
    if s["format"] == "MP3":
        if s["mode"].startswith("VBR"):
            lvl = MP3_VBR_LEVELS.index(s["vbr_level"]) if s["vbr_level"] in MP3_VBR_LEVELS else 2
            cmd += ["-q:a", str(lvl)]
        else:
            cmd += ["-b:a", s["bitrate"] + "k"]
        cmd += ["-write_xing", "1"]
    elif not fmt["lossless"]:
        cmd += ["-b:a", s["bitrate"] + "k"]
    if s["format"] == "FLAC":
        cmd += ["-sample_fmt", "s32" if s["bits"] == "24" else "s16"]
        if s["bits"] == "24":
            cmd += ["-bits_per_raw_sample", "24"]

    # Частота та канали
    if s["rate"] != "Як у вихідному":
        cmd += ["-ar", s["rate"]]
    if s["channels"] == "Стерео":
        cmd += ["-ac", "2"]
    elif s["channels"] == "Моно":
        cmd += ["-ac", "1"]

    # Фільтри
    filters = []
    if s.get("eq_apply"):
        filters += eq_filters(s.get("eq_gains"), info.get("sample_rate"))
    if s["normalize"]:
        filters.append("loudnorm=I=-14:TP=-1.0:LRA=11")
    if s["rate"] != "Як у вихідному":
        filters.append("aresample=resampler=soxr" if _has_soxr(ffmpeg) else "aresample")
    if filters:
        cmd += ["-af", ",".join(filters)]

    # Теги MP3
    if s["format"] == "MP3":
        if s.get("id3_only23") or s["id3"].startswith("ID3v2.3"):
            cmd += ["-id3v2_version", "3"]
        else:
            cmd += ["-id3v2_version", "4"]
        cmd += ["-write_id3v1", "1" if "ID3v1" in s["id3"] else "0"]

    if length is not None:
        cmd += ["-t", "%.3f" % length]
    cmd += ["-progress", "pipe:1", "-nostats", dst]
    return cmd


_soxr_cache = {}


def _has_soxr(ffmpeg):
    if ffmpeg not in _soxr_cache:
        try:
            p = subprocess.run([ffmpeg, "-hide_banner", "-buildconf"],
                               capture_output=True, **_popen_kwargs())
            _soxr_cache[ffmpeg] = b"libsoxr" in p.stdout
        except Exception:
            _soxr_cache[ffmpeg] = False
    return _soxr_cache[ffmpeg]


def output_name(src, out_dir, s, part=None, parts=None, name=None):
    base = os.path.splitext(os.path.basename(src))[0]
    ext = FORMATS[s["format"]]["ext"]
    folder = out_dir
    if part is not None and parts and parts > 1:
        if s.get("split_folder"):
            folder = os.path.join(out_dir, safe_name(base))
            base = "%02d - %s" % (part, safe_name(name) if name else base)
        elif name:
            base = "%s - %02d - %s" % (base, part, safe_name(name))
        else:
            base = "%s - частина %02d" % (base, part)
    elif name:
        base = "%s - %s" % (base, safe_name(name))
    else:
        base += "_car" if out_dir == os.path.dirname(src) else ""
    path = os.path.join(folder, base + ext)
    if os.path.abspath(path) == os.path.abspath(src):
        path = os.path.join(folder, base + "_new" + ext)
    return path


def plan_jobs(info, s, fsplit=None):
    """Розбиття на частини: повертає список (start, length, part, parts, name).
    fsplit — налаштування цього файлу: mode = global | none | manual."""
    dur = info["duration"]
    single = [(None, None, None, None, None)]
    fsplit = fsplit or {}
    mode = fsplit.get("mode", "global")
    if mode == "none":
        return single
    if mode == "manual":
        segs = sorted((sg for sg in fsplit.get("segs", []) if sg[0] < dur), key=lambda sg: sg[0])
        if not segs:
            return single
        n = len(segs)
        jobs = []
        for i, (st, en, name) in enumerate(segs):
            if en is None:  # порожній «Кінець» — до наступної ділянки або до кінця файлу
                en = segs[i + 1][0] if i + 1 < n else dur
            en = min(en, dur)
            jobs.append((st, max(0.1, en - st), i + 1, n, (name or "").strip() or None))
        return jobs
    try:
        minutes = float(str(s.get("split", "0")).replace(",", "."))
    except ValueError:
        minutes = 0
    if minutes <= 0 or dur <= minutes * 60 + 30:
        return single
    step = minutes * 60
    parts = int(dur // step) + (1 if dur % step > 30 else 0)
    jobs = []
    for i in range(parts):
        start = i * step
        length = (dur - start) if i == parts - 1 else step
        jobs.append((start, length, i + 1, parts, None))
    return jobs


def run_ffmpeg(cmd, total_seconds, on_progress, stop_event):
    """Запускає FFmpeg, повідомляє прогрес (0..1). Повертає (код, текст помилки)."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         **_popen_kwargs())
    err_lines = []

    def read_err():
        for line in p.stderr:
            err_lines.append(line.decode("utf-8", "replace").rstrip())
            del err_lines[:-30]

    t = threading.Thread(target=read_err, daemon=True)
    t.start()
    for raw in p.stdout:
        if stop_event.is_set():
            p.kill()
            break
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                us = int(line.split("=", 1)[1])
                if total_seconds > 0:
                    on_progress(min(1.0, us / 1e6 / total_seconds))
            except ValueError:
                pass
    p.wait()
    t.join(timeout=2)
    return p.returncode, "\n".join(err_lines[-8:])


# --------------------------------------------------------------------------
#  Інтерфейс (Windows-форма на tkinter)
# --------------------------------------------------------------------------
def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    cfg = load_config()
    settings = dict(DEFAULT_SETTINGS)
    settings.update(cfg.get("settings", {}))

    root = tk.Tk()
    root.title("%s %s" % (APP_NAME, APP_VERSION))
    for _ico in (os.path.join(getattr(sys, "_MEIPASS", app_dir()), "AudioConverter.ico"),
                 os.path.join(app_dir(), "AudioConverter.ico")):
        if os.name == "nt" and os.path.isfile(_ico):
            try:
                root.iconbitmap(default=_ico)
            except tk.TclError:
                pass
            break
    root.geometry("1240x960")
    root.minsize(1060, 820)
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    elif "clam" in style.theme_names():
        style.theme_use("clam")
    style.configure("Big.TButton", font=("Segoe UI", 10, "bold"), padding=6)
    style.configure("Treeview", rowheight=22)

    state = {"ffmpeg": None, "ffprobe": None, "files": [], "info": {},
             "edits": {},  # налаштування кожного файлу: теги, обкладинка, розбиття
             "worker": None, "stop": threading.Event()}
    ui_queue = queue.Queue()

    # ---------- FFmpeg ----------
    def locate_ffmpeg(show_error=False):
        hint = cfg.get("ffmpeg_dir")
        state["ffmpeg"] = find_tool("ffmpeg", hint)
        state["ffprobe"] = find_tool("ffprobe", hint)
        ok = bool(state["ffmpeg"] and state["ffprobe"])
        ff_var.set("FFmpeg: " + (os.path.dirname(state["ffmpeg"]) if ok else "НЕ ЗНАЙДЕНО"))
        ff_label.configure(foreground="#1b5e20" if ok else "#b71c1c")
        if not ok and show_error:
            messagebox.showwarning(
                APP_NAME,
                "Не знайдено ffmpeg.exe та ffprobe.exe.\n\n"
                "Варіанти:\n"
                "1) Запустіть «Install-FFmpeg.bat» з папки програми;\n"
                "2) або завантажте FFmpeg з https://www.gyan.dev/ffmpeg/builds/ "
                "і розпакуйте папку bin поруч із програмою (ffmpeg\\bin);\n"
                "3) або натисніть «Вказати FFmpeg…» і виберіть папку з ffmpeg.exe.")
        return ok

    def choose_ffmpeg():
        d = filedialog.askdirectory(title="Папка, де лежать ffmpeg.exe та ffprobe.exe")
        if d:
            cfg["ffmpeg_dir"] = d
            save_config(cfg)
            if not locate_ffmpeg():
                messagebox.showerror(APP_NAME, "У вибраній папці немає ffmpeg/ffprobe.")
            elif state["files"]:
                analyze_selected()

    # ---------- Верхня панель: файли ----------
    top = ttk.Frame(root, padding=(10, 8, 10, 0))
    top.pack(fill="x")
    files_box = ttk.LabelFrame(top, text=" Вхідні файли ", padding=6)
    files_box.pack(fill="x")

    btns = ttk.Frame(files_box)
    btns.pack(side="left", fill="y", padx=(0, 8))

    cols = ("name", "codec", "rate", "br", "dur", "status")
    flist = ttk.Treeview(files_box, columns=cols, show="headings", height=5, selectmode="extended")
    heads = {"name": ("Файл", 380), "codec": ("Кодек", 70), "rate": ("Частота", 80),
             "br": ("Бітрейт", 90), "dur": ("Тривалість", 85), "status": ("Сумісність / стан", 250)}
    for c in cols:
        flist.heading(c, text=heads[c][0])
        flist.column(c, width=heads[c][1], anchor="w" if c in ("name", "status") else "center")
    fsb = ttk.Scrollbar(files_box, orient="vertical", command=flist.yview)
    flist.configure(yscrollcommand=fsb.set)
    flist.pack(side="left", fill="both", expand=True)
    fsb.pack(side="left", fill="y")
    flist.tag_configure("error", foreground="#b71c1c")
    flist.tag_configure("warn", foreground="#a15c00")
    flist.tag_configure("ok", foreground="#1b5e20")

    def add_paths(paths):
        added = 0
        for p in paths:
            p = os.path.abspath(p)
            if p in state["files"] or not os.path.isfile(p):
                continue
            state["files"].append(p)
            flist.insert("", "end", iid=p, values=(os.path.basename(p), "…", "", "", "", "аналіз…"))
            added += 1
            threading.Thread(target=bg_probe, args=(p,), daemon=True).start()
        if added and not flist.selection():
            first = state["files"][-added]
            flist.selection_set(first)
            flist.focus(first)

    def add_files():
        if not state["ffprobe"] and not locate_ffmpeg(True):
            return
        paths = filedialog.askopenfilenames(
            title="Виберіть аудіофайли",
            initialdir=cfg.get("last_in_dir") or os.path.expanduser("~"),
            filetypes=[("Аудіофайли", " ".join("*" + e for e in AUDIO_EXT)), ("Усі файли", "*.*")])
        if paths:
            cfg["last_in_dir"] = os.path.dirname(paths[0])
            add_paths(paths)

    def add_folder():
        if not state["ffprobe"] and not locate_ffmpeg(True):
            return
        d = filedialog.askdirectory(title="Папка з музикою",
                                    initialdir=cfg.get("last_in_dir") or os.path.expanduser("~"))
        if not d:
            return
        cfg["last_in_dir"] = d
        found = []
        for dirpath, _dirs, files in os.walk(d):
            for f in sorted(files):
                if f.lower().endswith(AUDIO_EXT):
                    found.append(os.path.join(dirpath, f))
        if not found:
            messagebox.showinfo(APP_NAME, "У папці немає аудіофайлів.")
        add_paths(found)

    def remove_selected():
        for iid in flist.selection():
            flist.delete(iid)
            state["files"].remove(iid)
            state["info"].pop(iid, None)
            state["edits"].pop(iid, None)
            if pl["path"] == iid:
                player_stop()
        show_info(None)
        load_editors(None)

    def clear_all():
        if pl["path"] in state["files"]:
            player_stop()
        flist.delete(*flist.get_children())
        state["files"].clear()
        state["info"].clear()
        state["edits"].clear()
        show_info(None)
        load_editors(None)

    for text, cmd in (("Додати файли…", add_files), ("Додати папку…", add_folder),
                      ("Видалити", remove_selected), ("Очистити", clear_all)):
        ttk.Button(btns, text=text, command=cmd, width=16).pack(fill="x", pady=2)

    def bg_probe(path):
        try:
            info = probe_file(state["ffprobe"], path)
            ui_queue.put(("probed", path, info))
        except Exception as e:  # noqa: BLE001
            ui_queue.put(("probe_err", path, str(e)))

    # ---------- Програвач ----------
    # Звук відтворює ffplay (входить до FFmpeg). Перемотування, гучність і еквалайзер
    # застосовуються перезапуском ffplay з потрібної позиції — без сторонніх бібліотек.
    pl = {"path": None, "dur": 0.0, "pos": 0.0, "t0": 0.0, "start_pos": 0.0, "clock": None,
          "playing": False, "proc": None, "drag": False, "restart_job": None, "ended": False}
    eq_gains = [tk.DoubleVar(value=float(g)) for g in
                (list(settings.get("eq_gains") or []) + [0] * len(EQ_BANDS))[:len(EQ_BANDS)]]
    eq_apply = tk.BooleanVar(value=bool(settings.get("eq_apply")))
    eq_preset = tk.StringVar(value=settings.get("eq_preset") or "Плоский (без змін)")
    vol = tk.DoubleVar(value=float(settings.get("volume", 100)))
    muted = tk.BooleanVar(value=False)
    autonext = tk.BooleanVar(value=True)

    pbox = ttk.LabelFrame(root, text=" Програвач ", padding=(6, 2, 6, 4))
    pbox.pack(fill="x", padx=10, pady=(6, 0))
    prow = ttk.Frame(pbox)
    prow.pack(fill="x")
    btn_prev = ttk.Button(prow, text="|◀", width=3)
    btn_play = ttk.Button(prow, text="▶", width=3)
    btn_stopp = ttk.Button(prow, text="■", width=3)
    btn_next = ttk.Button(prow, text="▶|", width=3)
    for w in (btn_prev, btn_play, btn_stopp, btn_next):
        w.pack(side="left", padx=1)
    time_var = tk.StringVar(value="0:00 / 0:00")
    ttk.Label(prow, textvariable=time_var, width=17, anchor="center",
              font=("Consolas", 10)).pack(side="left", padx=6)
    timeline = tk.Canvas(prow, height=24, highlightthickness=0, cursor="hand2", background="#eceff1")
    timeline.pack(side="left", fill="x", expand=True, padx=4)
    btn_mute = ttk.Button(prow, text="♪", width=3)
    btn_mute.pack(side="left", padx=(6, 0))
    vol_scale = ttk.Scale(prow, from_=0, to=200, variable=vol, length=110)
    vol_scale.pack(side="left", padx=2)
    vol_lbl = ttk.Label(prow, width=5)
    vol_lbl.pack(side="left")
    btn_eq = ttk.Button(prow, text="Еквалайзер…")
    btn_eq.pack(side="left", padx=4)
    prow2 = ttk.Frame(pbox)
    prow2.pack(fill="x")
    now_var = tk.StringVar(value="Двічі клацніть файл у списку або натисніть ▶.")
    ttk.Label(prow2, textvariable=now_var, foreground="#37474f").pack(side="left")
    ttk.Button(prow2, text="Відкрити інший файл…", command=lambda: player_open_other()).pack(side="right")
    ttk.Checkbutton(prow2, text="Далі по списку", variable=autonext).pack(side="right", padx=8)
    btn_mark_end = ttk.Button(prow2, text="→ Кінець ділянки", command=lambda: mark_pos("end"))
    btn_mark_end.pack(side="right", padx=2)
    btn_mark_start = ttk.Button(prow2, text="→ Початок ділянки", command=lambda: mark_pos("start"))
    btn_mark_start.pack(side="right", padx=2)
    ttk.Label(prow2, text="Поточну позицію:", foreground="#607d8b").pack(side="right", padx=(0, 2))

    def player_filters():
        f = []
        info = state["info"].get(pl["path"])
        sr = info.get("sample_rate") if isinstance(info, dict) else 44100
        f += eq_filters([g.get() for g in eq_gains], sr)
        level = 0.0 if muted.get() else vol.get() / 100.0
        f.insert(0, "volume=%.2f" % level)
        return ",".join(f)

    def player_pos():
        if pl["playing"]:
            if pl["clock"] is not None:
                base, at_t = pl["clock"]
                p = base + (time.monotonic() - at_t)
            else:
                p = pl["start_pos"] + (time.monotonic() - pl["t0"])
            return max(0.0, min(p, pl["dur"] or p))
        return pl["pos"]

    def kill_proc():
        p = pl["proc"]
        pl["proc"] = None
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    def read_clock(proc):
        """Читає з ffplay рядки стану «  103.22 M-A: …» — точний поточний час."""
        buf = b""
        while True:
            ch = proc.stderr.read(1)
            if not ch:
                break
            if ch in (b"\r", b"\n"):
                m = re.match(rb"\s*(-?\d+\.\d+)\s+(M-A|A-V|M-V)", buf)
                if m and pl["proc"] is proc:
                    pl["clock"] = (float(m.group(1)), time.monotonic())
                buf = b""
            else:
                buf += ch

    def start_proc(pos):
        kill_proc()
        ffplay = find_tool("ffplay", cfg.get("ffmpeg_dir"))
        if not ffplay:
            messagebox.showinfo(APP_NAME, "Для програвача потрібен ffplay.exe — він є в архіві FFmpeg "
                                          "поруч з ffmpeg.exe (папка bin). Запустіть «Install-FFmpeg.bat» "
                                          "або покладіть ffplay.exe поруч з ffmpeg.exe.")
            pl["playing"] = False
            update_player_ui()
            return
        cmd = [ffplay, "-hide_banner", "-nodisp", "-autoexit", "-loglevel", "info",
               "-ss", "%.2f" % pos, "-af", player_filters(), pl["path"]]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                    **_popen_kwargs())
        except OSError as e:
            messagebox.showerror(APP_NAME, "Не вдалося запустити ffplay:\n%s" % e)
            pl["playing"] = False
            return
        pl.update(proc=proc, start_pos=pos, t0=time.monotonic(), clock=None, playing=True, ended=False)
        threading.Thread(target=read_clock, args=(proc,), daemon=True).start()
        update_player_ui()

    def player_load(path, play=False, pos=0.0):
        info = state["info"].get(path)
        if not isinstance(info, dict):
            try:
                info = probe_file(state["ffprobe"], path)
            except Exception as e:  # noqa: BLE001
                messagebox.showerror(APP_NAME, "Не вдалося відкрити файл:\n%s" % e)
                return
        if pl["path"] != path:
            kill_proc()
            pl.update(path=path, dur=info["duration"], pos=pos, playing=False)
            pl["ext_info"] = info
        else:
            pl["pos"] = pos
        if play:
            start_proc(pos)
        update_player_ui()

    def player_toggle():
        if pl["playing"]:
            pl["pos"] = player_pos()
            pl["playing"] = False
            kill_proc()
        else:
            sel = flist.focus() or (state["files"][0] if state["files"] else None)
            if not pl["path"]:
                if not sel:
                    player_open_other()
                    return
                player_load(sel)
            pos = pl["pos"] if pl["pos"] < (pl["dur"] or 1) - 0.5 else 0.0
            start_proc(pos)
        update_player_ui()

    def player_stop():
        kill_proc()
        pl.update(playing=False, pos=0.0)
        update_player_ui()

    def player_step(d):
        files = [p for p in state["files"] if isinstance(state["info"].get(p), dict)]
        if not files:
            return
        if pl["path"] in files:
            i = files.index(pl["path"]) + d
        else:
            i = 0
        if d < 0 and pl["path"] in files and player_pos() > 3:
            i += 1  # «⏮» на початку пісні — на початок цієї ж пісні
        i = max(0, min(len(files) - 1, i))
        was = pl["playing"] or d != 0
        player_load(files[i], play=was, pos=0.0)
        flist.selection_set(files[i])
        flist.focus(files[i])
        flist.see(files[i])

    def player_seek(sec, restart=True):
        if not pl["path"]:
            return
        sec = max(0.0, min(sec, max(0.0, pl["dur"] - 0.2)))
        pl["pos"] = sec
        if pl["playing"] and restart:
            start_proc(sec)
        update_player_ui()

    def player_open_other():
        path = filedialog.askopenfilename(
            title="Відкрити файл для прослуховування",
            initialdir=last_out.get("dir") or cfg.get("last_in_dir") or os.path.expanduser("~"),
            filetypes=[("Аудіофайли", " ".join("*" + e for e in AUDIO_EXT)), ("Усі файли", "*.*")])
        if path:
            player_load(path, play=True)

    def restart_soon(delay=250):
        """Після зміни гучності/еквалайзера — перезапуск з поточної позиції."""
        if pl["restart_job"]:
            root.after_cancel(pl["restart_job"])

        def go():
            pl["restart_job"] = None
            if pl["playing"]:
                start_proc(player_pos())
        pl["restart_job"] = root.after(delay, go)

    def mark_pos(which):
        if not pl["path"]:
            return
        if pl["path"] != cur.get("path"):
            if pl["path"] in state["files"]:
                flist.selection_set(pl["path"])
                flist.focus(pl["path"])
                on_select()
            else:
                messagebox.showinfo(APP_NAME, "Цей файл не в списку — ділянки задаються для файлів зі списку.")
                return
        (seg_start if which == "start" else seg_end).set(fmt_ts(round(player_pos(), 1)))
        nb.select(2)

    # --- таймлайн ---
    def draw_timeline():
        c = timeline
        c.delete("all")
        w = max(c.winfo_width(), 10)
        h = int(c.cget("height"))
        dur = pl["dur"] or 0
        c.create_rectangle(0, 8, w, h - 8, fill="#cfd8dc", outline="")
        if not dur:
            return
        x = int(w * min(1.0, (pl["drag_pos"] if pl["drag"] else player_pos()) / dur))
        c.create_rectangle(0, 8, x, h - 8, fill="#1e88e5", outline="")
        # межі ділянок файлу (вкладка «Розбиття»)
        ed = state["edits"].get(pl["path"])
        if ed and ed["split"]["segs"]:
            for i, (st, en, _n) in enumerate(ed["split"]["segs"]):
                for t, col in ((st, "#e65100"), (en, "#6d4c41")):
                    if t is not None and 0 <= t <= dur:
                        xx = int(w * t / dur)
                        c.create_line(xx, 2, xx, h - 2, fill=col, width=2)
        c.create_oval(x - 6, h / 2 - 6, x + 6, h / 2 + 6, fill="#0d47a1", outline="white")

    def tl_time(event):
        w = max(timeline.winfo_width(), 1)
        return max(0.0, min(1.0, event.x / float(w))) * (pl["dur"] or 0)

    def tl_press(event):
        if not pl["path"]:
            return
        pl["drag"] = True
        pl["drag_pos"] = tl_time(event)
        update_player_ui()

    def tl_motion(event):
        if pl["drag"]:
            pl["drag_pos"] = tl_time(event)
            update_player_ui()

    def tl_release(event):
        if pl["drag"]:
            pl["drag"] = False
            player_seek(tl_time(event))

    timeline.bind("<Button-1>", tl_press)
    timeline.bind("<B1-Motion>", tl_motion)
    timeline.bind("<ButtonRelease-1>", tl_release)
    timeline.bind("<Configure>", lambda e: draw_timeline())

    def update_player_ui():
        dur = pl["dur"] or 0
        p = pl["drag_pos"] if pl["drag"] else player_pos()
        time_var.set("%s / %s" % (fmt_ts(int(p)), fmt_ts(int(dur))))
        btn_play.configure(text="||" if pl["playing"] else "▶")
        if pl["path"]:
            now_var.set(("Грає: " if pl["playing"] else "Пауза: " if p > 0 else "Вибрано: ")
                        + os.path.basename(pl["path"]))
        vol_lbl.configure(text="%d%%" % round(vol.get()))
        btn_mute.configure(text="×" if muted.get() else "♪")
        draw_timeline()

    def player_tick():
        if pl["playing"]:
            proc = pl["proc"]
            if proc is not None and proc.poll() is not None:  # пісня закінчилась
                pl.update(playing=False, pos=0.0, proc=None)
                if autonext.get() and pl["path"] in state["files"] and \
                        state["files"].index(pl["path"]) < len(state["files"]) - 1:
                    player_step(1)
                else:
                    now_var.set("Кінець: " + os.path.basename(pl["path"]))
            update_player_ui()
        root.after(200, player_tick)

    pl["drag_pos"] = 0.0
    btn_play.configure(command=player_toggle)
    btn_stopp.configure(command=player_stop)
    btn_prev.configure(command=lambda: player_step(-1))
    btn_next.configure(command=lambda: player_step(1))

    # --- колесо миші: гучність ±5 %, смуги еквалайзера ±1 дБ, таймлайн ±5 с ---
    wheel_targets = {}

    def wheel_dir(event):
        if getattr(event, "num", None) == 4:
            return 1
        if getattr(event, "num", None) == 5:
            return -1
        return 1 if event.delta > 0 else -1 if event.delta < 0 else 0

    def wheel_bind(widget, action):
        """action(напрямок: +1 вгору / −1 вниз). Працює і коли Windows надсилає
        подію колеса віджету з фокусом, а не тому, що під курсором."""
        wheel_targets[str(widget)] = action

        def on_wheel(event):
            d = wheel_dir(event)
            if d:
                action(d)
            return "break"
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(seq, on_wheel)

    def wheel_router(event):
        try:
            w = root.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            return None
        if w is not None and str(w) in wheel_targets and w is not event.widget:
            d = wheel_dir(event)
            if d:
                wheel_targets[str(w)](d)
            return "break"
        return None

    for _seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        root.bind_all(_seq, wheel_router, add="+")

    def wheel_volume(d):
        vol.set(max(0.0, min(200.0, round(vol.get() / 5.0) * 5 + 5 * d)))

    wheel_bind(vol_scale, wheel_volume)
    wheel_bind(vol_lbl, wheel_volume)
    wheel_bind(btn_mute, wheel_volume)

    def wheel_timeline(d):
        if not pl["path"]:
            return
        p = max(0.0, min(player_pos() + 5 * d, max(0.0, pl["dur"] - 0.2)))
        was = pl["playing"] or pl.get("wheel_resume")
        kill_proc()
        pl.update(playing=False, pos=p)
        update_player_ui()
        if was:
            pl["wheel_resume"] = True
            if pl.get("wheel_job"):
                root.after_cancel(pl["wheel_job"])

            def go():
                pl["wheel_job"] = None
                pl["wheel_resume"] = False
                start_proc(pl["pos"])
            pl["wheel_job"] = root.after(350, go)
    wheel_bind(timeline, wheel_timeline)

    def toggle_mute():
        muted.set(not muted.get())
        update_player_ui()
        restart_soon(50)
    btn_mute.configure(command=toggle_mute)

    def on_vol(*_):
        vol_lbl.configure(text="%d%%" % round(vol.get()))
        restart_soon(300)
    vol.trace_add("write", on_vol)

    # --- вікно еквалайзера ---
    eq_win = {"w": None}

    def apply_eq_preset(name):
        vals = EQ_PRESETS.get(name)
        if vals:
            for var, g in zip(eq_gains, vals):
                var.set(float(g))
            restart_soon()

    def open_eq():
        if eq_win["w"] and eq_win["w"].winfo_exists():
            eq_win["w"].lift()
            return
        w = tk.Toplevel(root)
        eq_win["w"] = w
        w.title("Еквалайзер")
        w.transient(root)
        w.resizable(False, False)
        top_r = ttk.Frame(w, padding=(10, 8, 10, 0))
        top_r.pack(fill="x")
        ttk.Label(top_r, text="Готові налаштування:").pack(side="left")
        cb = ttk.Combobox(top_r, textvariable=eq_preset, values=list(EQ_PRESETS), state="readonly", width=26)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: apply_eq_preset(eq_preset.get()))
        ttk.Button(top_r, text="Скинути", command=lambda: (eq_preset.set("Плоский (без змін)"),
                                                             apply_eq_preset("Плоский (без змін)"))).pack(side="left")
        bands = ttk.Frame(w, padding=10)
        bands.pack()
        for i, (f, var) in enumerate(zip(EQ_BANDS, eq_gains)):
            col = ttk.Frame(bands)
            col.grid(row=0, column=i, padx=4)
            val_lbl = ttk.Label(col, width=5, anchor="center")
            val_lbl.pack()
            sc = ttk.Scale(col, from_=12, to=-12, orient="vertical", variable=var, length=170)
            sc.pack()
            ttk.Label(col, text=("%dk" % (f // 1000)) if f >= 1000 else str(f)).pack()

            def upd(*_a, var=var, lbl=val_lbl):
                lbl.configure(text="%+d" % round(var.get()) if round(var.get()) else "0")
            upd()
            var.trace_add("write", upd)
            sc.bind("<ButtonRelease-1>", lambda e: restart_soon(50))
            sc.bind("<Double-Button-1>", lambda e, var=var: (var.set(0.0), restart_soon(50)))

            def wheel_band(d, var=var):
                var.set(max(-12.0, min(12.0, round(var.get()) + d)))
            wheel_bind(sc, wheel_band)
            wheel_bind(val_lbl, wheel_band)
        ttk.Label(w, text="дБ: від −12 до +12. Подвійне клацання по повзунку — 0. Зміни чутно одразу "
                          "(з короткою паузою).", foreground="#607d8b", padding=(10, 0)).pack(anchor="w")
        ttk.Checkbutton(w, text="Застосовувати еквалайзер і під час перекодування (змінює звук у нових файлах)",
                        variable=eq_apply, padding=(10, 6)).pack(anchor="w")
        ttk.Button(w, text="Закрити", command=w.destroy).pack(anchor="e", padx=10, pady=(0, 10))

    btn_eq.configure(command=open_eq)
    def sync_preset_name():
        cur_vals = [round(g.get()) for g in eq_gains]
        for name, vals in EQ_PRESETS.items():
            if cur_vals == list(vals):
                eq_preset.set(name)
                return
        eq_preset.set("Власне")

    for var in eq_gains:
        var.trace_add("write", lambda *_a: restart_soon(400) if not pl["drag"] else None)
        var.trace_add("write", lambda *_a: sync_preset_name())

    eq_apply.trace_add("write", lambda *_a: update_estimate())
    for var in eq_gains:
        var.trace_add("write", lambda *_a: eq_apply.get() and update_estimate())
    root.after(300, player_tick)

    # ---------- Середня частина ----------
    mid = ttk.Frame(root, padding=(10, 8, 10, 0))
    mid.pack(fill="both", expand=True)
    mid.columnconfigure(0, weight=1)
    mid.columnconfigure(1, weight=1)
    mid.rowconfigure(0, weight=1)

    # Ліворуч: вкладки вибраного файлу — параметри, теги й обкладинка, розбиття
    in_box = ttk.LabelFrame(mid, text=" Вибраний файл ", padding=6)
    in_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
    nb = ttk.Notebook(in_box)
    nb.pack(fill="both", expand=True)
    tab_info = ttk.Frame(nb, padding=4)
    tab_tags = ttk.Frame(nb, padding=6)
    tab_split = ttk.Frame(nb, padding=6)
    nb.add(tab_info, text=" Параметри ")
    nb.add(tab_tags, text=" Теги й обкладинка ")
    nb.add(tab_split, text=" Розбиття на ділянки ")
    tree_wrap = ttk.Frame(tab_info)
    tree_wrap.pack(fill="both", expand=True)
    info_tree = ttk.Treeview(tree_wrap, columns=("k", "v"), show="", height=10)
    info_tree.column("k", width=170, anchor="w")
    info_tree.column("v", width=330, anchor="w")
    info_tree.pack(side="left", fill="both", expand=True)
    isb = ttk.Scrollbar(tree_wrap, orient="vertical", command=info_tree.yview)
    info_tree.configure(yscrollcommand=isb.set)
    isb.pack(side="left", fill="y")
    ttk.Label(tab_info, text="Перевірка сумісності з автомагнітолою:",
              font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(6, 2))
    compat = tk.Text(tab_info, height=7, wrap="word", relief="flat",
                     background=root.cget("background"), font=("Segoe UI", 9))
    compat.pack(fill="x")
    compat.tag_configure("error", foreground="#b71c1c")
    compat.tag_configure("warn", foreground="#a15c00")
    compat.tag_configure("info", foreground="#455a64")
    compat.tag_configure("ok", foreground="#1b5e20")
    compat.configure(state="disabled")

    def show_info(path):
        info_tree.delete(*info_tree.get_children())
        compat.configure(state="normal")
        compat.delete("1.0", "end")
        if not path or path not in state["info"]:
            if path:
                info_tree.insert("", "end", values=("", "аналіз…"))
            compat.configure(state="disabled")
            return
        info = state["info"][path]
        if isinstance(info, str):
            info_tree.insert("", "end", values=("Помилка", info))
            compat.configure(state="disabled")
            return
        for k, v in describe(info):
            info_tree.insert("", "end", values=(k, v))
        icons = {"error": "✖ ", "warn": "⚠ ", "info": "ℹ ", "ok": "✔ "}
        for lvl, text in compat_issues(info, state["edits"].get(path), current_settings()):
            compat.insert("end", icons[lvl] + text + "\n", lvl)
        compat.configure(state="disabled")

    def analyze_selected():
        for p in state["files"]:
            if p not in state["info"]:
                threading.Thread(target=bg_probe, args=(p,), daemon=True).start()

    # ================= Вкладка «Теги й обкладинка» =================
    cur = {"path": None, "loading": False}

    def cur_edits():
        p = cur["path"]
        return state["edits"].get(p) if p else None

    tags_grid = ttk.Frame(tab_tags)
    tags_grid.pack(fill="x")
    tags_grid.columnconfigure(1, weight=1)
    tags_grid.columnconfigure(3, weight=1)
    tag_vars = {}
    tag_widgets = []
    # Назва/Виконавець/Альбом/Виконавець альбому — на всю ширину; Рік, Жанр, № — у рядок
    layout = {"title": (0, 0, 3), "artist": (1, 0, 3), "album": (2, 0, 3), "album_artist": (3, 0, 3),
              "date": (4, 0, 1), "genre": (4, 2, 1), "track": (5, 0, 1)}
    for k, label in TAG_FIELDS:
        r, c, span = layout[k]
        ttk.Label(tags_grid, text=label + ":").grid(row=r, column=c, sticky="w", pady=2, padx=(0 if c == 0 else 10, 6))
        var = tk.StringVar()
        tag_vars[k] = var
        if k == "genre":
            w = ttk.Combobox(tags_grid, textvariable=var, values=GENRES)
        else:
            w = ttk.Entry(tags_grid, textvariable=var)
        w.grid(row=r, column=c + 1, columnspan=span, sticky="we", pady=2)
        tag_widgets.append(w)
        var.trace_add("write", lambda *_a, key=k: on_tag_change(key))
    ttk.Label(tags_grid, text="напр. 3 або 3/12", foreground="#607d8b").grid(row=5, column=2, columnspan=2,
                                                                       sticky="w", padx=10)

    def on_tag_change(key):
        ed = cur_edits()
        if cur["loading"] or not ed:
            return
        ed["tags"][key] = tag_vars[key].get().strip()
        refresh_compat_soon()

    _compat_job = {"id": None}

    def refresh_compat_soon():
        if _compat_job["id"]:
            root.after_cancel(_compat_job["id"])
        _compat_job["id"] = root.after(400, lambda: show_info(cur["path"]))

    def tags_from_filename():
        p = cur["path"]
        if not p or not cur_edits():
            return
        base = os.path.splitext(os.path.basename(p))[0].replace("_", " ")
        base = re.sub(r"^\s*\d{1,3}\s*[.)\-–]?\s+", "", base)  # «01 - », «01. »
        for sep in (" - ", " – ", " — "):
            if sep in base:
                artist, title = base.split(sep, 1)
                tag_vars["artist"].set(artist.strip())
                tag_vars["title"].set(title.strip())
                return
        tag_vars["title"].set(base.strip())

    def tags_reset():
        ed = cur_edits()
        if not ed:
            return
        ed["tags"] = dict(ed["orig"])
        load_editors(cur["path"])

    def tags_to_all():
        ed = cur_edits()
        if not ed:
            return
        others = [p for p in state["files"] if p != cur["path"] and p in state["edits"]]
        if not others:
            messagebox.showinfo(APP_NAME, "У списку немає інших файлів.")
            return
        if not messagebox.askyesno(APP_NAME, "Скопіювати «Альбом», «Виконавець альбому», «Рік», «Жанр» "
                                             "і вибір обкладинки з цього файлу в усі інші файли списку "
                                             "(%d)?\n\nНазви, виконавці й номери треків не змінюються."
                                   % len(others)):
            return
        for p in others:
            o = state["edits"][p]
            for k in ("album", "album_artist", "date", "genre"):
                o["tags"][k] = ed["tags"].get(k, "")
            o["cover"] = dict(ed["cover"])
        status.set("Альбом, рік, жанр і обкладинку скопійовано в " + plural(len(others), "файл", "файли", "файлів") + ".")

    def number_tracks():
        files = [p for p in state["files"] if p in state["edits"]]
        n = len(files)
        if not n:
            return
        for i, p in enumerate(files, 1):
            state["edits"][p]["tags"]["track"] = "%d/%d" % (i, n)
        load_editors(cur["path"])
        status.set("Треки пронумеровано за порядком у списку: 1…%d." % n)

    tbtns = ttk.Frame(tab_tags)
    tbtns.pack(fill="x", pady=(6, 0))
    for i, (text, cmd) in enumerate((("Заповнити з імені файлу", tags_from_filename),
                                     ("Повернути теги файлу", tags_reset),
                                     ("Альбом/рік/жанр/обкладинку → усім", tags_to_all),
                                     ("Пронумерувати за списком", number_tracks))):
        ttk.Button(tbtns, text=text, command=cmd).grid(row=i // 2, column=i % 2, sticky="we", padx=2, pady=2)
    tbtns.columnconfigure(0, weight=1)
    tbtns.columnconfigure(1, weight=1)
    ttk.Label(tab_tags, text="Ім'я файлу «Виконавець - Назва» → теги. Порожнє поле — тег не записується. "
                             "Оригінал не змінюється: теги потрапляють лише в новий файл.",
              foreground="#607d8b", wraplength=520, justify="left").pack(anchor="w", pady=(2, 4))

    cov_box = ttk.LabelFrame(tab_tags, text=" Обкладинка ", padding=6)
    cov_box.pack(fill="x")
    cov_frame = tk.Frame(cov_box, width=154, height=154, relief="solid", borderwidth=1, background="#eceff1")
    cov_frame.pack(side="left", padx=(0, 10))
    cov_frame.pack_propagate(False)
    cov_img = tk.Label(cov_frame, text="немає", background="#eceff1", foreground="#78909c")
    cov_img.pack(fill="both", expand=True)
    cov_right = ttk.Frame(cov_box)
    cov_right.pack(side="left", fill="both", expand=True)
    cover_mode = tk.StringVar(value="global")
    cov_radios = {}
    for val, text in (("global", "Як у загальних налаштуваннях (галочка праворуч)"),
                      ("source", "Залишити обкладинку з файлу"),
                      ("file", "Своє зображення (JPG, PNG…)"),
                      ("none", "Без обкладинки")):
        rb = ttk.Radiobutton(cov_right, text=text, value=val, variable=cover_mode,
                             command=lambda: on_cover_mode())
        rb.pack(anchor="w", pady=1)
        cov_radios[val] = rb
    cov_btn_row = ttk.Frame(cov_right)
    cov_btn_row.pack(anchor="w", fill="x", pady=(4, 0))
    cov_info = tk.StringVar()
    ttk.Label(cov_right, textvariable=cov_info, foreground="#37474f", wraplength=330,
              justify="left").pack(anchor="w", pady=(4, 0))

    def choose_cover():
        ed = cur_edits()
        if not ed:
            return
        path = filedialog.askopenfilename(
            title="Зображення обкладинки",
            initialdir=cfg.get("last_cover_dir") or cfg.get("last_in_dir") or os.path.expanduser("~"),
            filetypes=[("Зображення", "*.jpg *.jpeg *.png *.bmp *.webp *.gif"), ("Усі файли", "*.*")])
        if not path:
            return
        cfg["last_cover_dir"] = os.path.dirname(path)
        ed["cover"] = {"mode": "file", "path": path}
        cover_mode.set("file")
        update_cover_view()
        refresh_compat_soon()

    def cover_from_folder():
        """Шукає cover.jpg / folder.jpg / front.jpg поруч із файлом."""
        ed = cur_edits()
        if not ed:
            return
        d = os.path.dirname(cur["path"])
        names = ("cover", "folder", "front", "albumart", "обкладинка")
        try:
            files = os.listdir(d)
        except OSError:
            files = []
        found = [f for f in files if os.path.splitext(f)[0].lower() in names
                 and f.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".webp"))]
        if not found:
            messagebox.showinfo(APP_NAME, "Поруч із файлом немає cover.jpg / folder.jpg / front.jpg.")
            return
        ed["cover"] = {"mode": "file", "path": os.path.join(d, found[0])}
        cover_mode.set("file")
        update_cover_view()
        refresh_compat_soon()

    ttk.Button(cov_btn_row, text="Вибрати зображення…", command=choose_cover).pack(side="left")
    ttk.Button(cov_btn_row, text="Знайти в папці", command=cover_from_folder).pack(side="left", padx=4)

    def on_cover_mode():
        ed = cur_edits()
        if cur["loading"] or not ed:
            return
        mode = cover_mode.get()
        if mode == "file" and not ed["cover"].get("path"):
            choose_cover()
            if not ed["cover"].get("path"):
                cover_mode.set(ed["cover"]["mode"])
            return
        ed["cover"]["mode"] = mode
        update_cover_view()
        refresh_compat_soon()

    thumbs = {"dir": None, "cache": {}, "want": None, "img": None}

    def thumb_dir():
        if not thumbs["dir"]:
            thumbs["dir"] = tempfile.mkdtemp(prefix="acar_")
        return thumbs["dir"]

    def show_thumb(png):
        try:
            thumbs["img"] = tk.PhotoImage(file=png)
            cov_img.configure(image=thumbs["img"], text="")
        except tk.TclError:
            cov_img.configure(image="", text="не вдалося\nпоказати")

    def update_cover_view():
        """Що саме піде в результат і мініатюра цього зображення."""
        ed = cur_edits()
        path = cur["path"]
        info = state["info"].get(path)
        if not ed or not isinstance(info, dict):
            cov_img.configure(image="", text="немає")
            cov_info.set("")
            return
        s = current_settings()
        fmt_ok = FORMATS[s["format"]]["ext"] in (".mp3", ".m4a", ".flac")
        mode = ed["cover"]["mode"]
        src, from_audio = None, False
        if mode == "file" and ed["cover"].get("path"):
            src = ed["cover"]["path"]
            note = "Своє зображення: %s" % os.path.basename(src)
        elif mode in ("source", "global") and info.get("cover") and (mode == "source" or s["keep_cover"]):
            src, from_audio = path, True
            note = "Обкладинка з файлу (%s)" % info["cover_size"]
        elif mode == "none":
            note = "Обкладинку не буде записано."
        elif info.get("cover"):
            note = "У файлі є обкладинка (%s), але галочку «Зберегти обкладинку» вимкнено — " \
                   "її не буде записано." % info["cover_size"]
        else:
            note = "У файлі немає обкладинки. Можна вибрати своє зображення."
        if src and from_audio and s.get("cover_resize"):
            note += "\nУ результаті: JPEG, не більше %d×%d." % (COVER_MAX, COVER_MAX)
            m = re.match(r"(\d+)x(\d+)", info.get("cover_size") or "")
            if m:
                w2, h2 = fit_cover(int(m.group(1)), int(m.group(2)))
                if (w2, h2) != (int(m.group(1)), int(m.group(2))):
                    note += " Буде зменшено пропорційно до %d×%d." % (w2, h2)
        elif src and not from_audio:
            note += "\nУ результаті: JPEG; якщо більше %d×%d — зменшиться пропорційно." % (COVER_MAX, COVER_MAX)
        if src and not fmt_ok:
            note += "\nФормат %s обкладинку не зберігає (лише MP3, M4A, FLAC)." % s["format"]
        cov_info.set(note)
        if not src or not state["ffmpeg"]:
            cov_img.configure(image="", text="немає" if not src else "…")
            thumbs["want"] = None
            return
        key = (src, from_audio)
        thumbs["want"] = key
        if key in thumbs["cache"]:
            show_thumb(thumbs["cache"][key])
            return
        cov_img.configure(image="", text="…")
        png = os.path.join(thumb_dir(), "t%d.png" % len(thumbs["cache"]))
        thumbs["cache"][key] = png

        def job():
            ok = make_thumb(state["ffmpeg"], src, png, from_audio)
            size = "" if from_audio else image_size(state["ffprobe"], src)
            ui_queue.put(("thumb", key, png if ok else None, size))
        threading.Thread(target=job, daemon=True).start()

    # ================= Вкладка «Розбиття на ділянки» =================
    split_mode = tk.StringVar(value="global")
    sm_frame = ttk.Frame(tab_split)
    sm_frame.pack(fill="x")
    sm_global = ttk.Radiobutton(sm_frame, value="global", variable=split_mode,
                                command=lambda: on_split_mode())
    sm_global.pack(anchor="w")
    ttk.Radiobutton(sm_frame, text="Не розбивати цей файл", value="none", variable=split_mode,
                    command=lambda: on_split_mode()).pack(anchor="w")
    ttk.Radiobutton(sm_frame, text="Вручну — за ділянками зі списку нижче", value="manual",
                    variable=split_mode, command=lambda: on_split_mode()).pack(anchor="w")

    seg_wrap = ttk.Frame(tab_split)
    seg_wrap.pack(fill="both", expand=True, pady=(4, 2))
    seg_cols = ("n", "start", "end", "len", "name")
    seg_tree = ttk.Treeview(seg_wrap, columns=seg_cols, show="headings", height=6, selectmode="extended")
    for c, (t, w) in zip(seg_cols, (("№", 36), ("Початок", 80), ("Кінець", 80), ("Тривалість", 80),
                                    ("Назва частини", 220))):
        seg_tree.heading(c, text=t)
        seg_tree.column(c, width=w, anchor="w" if c == "name" else "center", stretch=(c == "name"))
    seg_sb = ttk.Scrollbar(seg_wrap, orient="vertical", command=seg_tree.yview)
    seg_tree.configure(yscrollcommand=seg_sb.set)
    seg_tree.pack(side="left", fill="both", expand=True)
    seg_sb.pack(side="left", fill="y")

    ed_row = ttk.Frame(tab_split)
    ed_row.pack(fill="x", pady=2)
    seg_start, seg_end, seg_name = tk.StringVar(), tk.StringVar(), tk.StringVar()
    ttk.Label(ed_row, text="Початок").grid(row=0, column=0, sticky="w")
    ttk.Entry(ed_row, textvariable=seg_start, width=10).grid(row=1, column=0, sticky="w", padx=(0, 4))
    ttk.Label(ed_row, text="Кінець").grid(row=0, column=1, sticky="w")
    ttk.Entry(ed_row, textvariable=seg_end, width=10).grid(row=1, column=1, sticky="w", padx=(0, 4))
    ttk.Label(ed_row, text="Назва (необов'язково)").grid(row=0, column=2, sticky="w")
    ttk.Entry(ed_row, textvariable=seg_name).grid(row=1, column=2, sticky="we")
    ed_row.columnconfigure(2, weight=1)

    seg_btns = ttk.Frame(tab_split)
    seg_btns.pack(fill="x", pady=2)
    tools = ttk.Frame(tab_split)
    tools.pack(fill="x", pady=2)
    listen = ttk.Frame(tab_split)
    listen.pack(fill="x", pady=2)
    seg_artist = tk.BooleanVar(value=False)
    ttk.Checkbutton(tab_split, text="Назви виду «Виконавець - Пісня» → окремі теги «Виконавець» і «Назва»",
                    variable=seg_artist, command=lambda: on_seg_artist()).pack(anchor="w")
    seg_summary = tk.StringVar()
    ttk.Label(tab_split, textvariable=seg_summary, foreground="#37474f", wraplength=520,
              justify="left").pack(anchor="w", pady=(2, 0))
    ttk.Label(tab_split, text="Час: 1:02:03, 45:10 або 90 (секунди). Порожній «Кінець» — до кінця файлу "
                              "або до наступної ділянки. Проміжки між ділянками не потраплять у результат.",
              foreground="#607d8b", wraplength=520, justify="left").pack(anchor="w")

    def cur_duration():
        info = state["info"].get(cur["path"])
        return info["duration"] if isinstance(info, dict) else 0

    def segs():
        ed = cur_edits()
        return ed["split"]["segs"] if ed else []

    def normalize_segs(lst):
        """Сортування; порожній «Кінець» — до наступної ділянки."""
        lst.sort(key=lambda sg: sg[0])
        return lst

    def render_segs(select=None):
        seg_tree.delete(*seg_tree.get_children())
        dur = cur_duration()
        lst = segs()
        problems = []
        total = 0.0
        for i, (st, en, name) in enumerate(lst):
            nxt = lst[i + 1][0] if i + 1 < len(lst) else None
            real_end = en if en is not None else (nxt if nxt is not None else dur)
            real_end = min(real_end, dur) if dur else real_end
            length = max(0.0, real_end - st)
            total += length
            if dur and st >= dur:
                problems.append("№%d починається після кінця файлу" % (i + 1))
            if nxt is not None and real_end > nxt + 0.05:
                problems.append("№%d і №%d перекриваються" % (i + 1, i + 2))
            seg_tree.insert("", "end", iid=str(i), values=(
                i + 1, fmt_ts(st), fmt_ts(en) if en is not None else "→ " + fmt_ts(real_end),
                fmt_ts(length), name))
        if select is not None and seg_tree.exists(str(select)):
            seg_tree.selection_set(str(select))
            seg_tree.see(str(select))
        if pl["path"] == cur["path"]:
            draw_timeline()
        if not cur["path"]:
            seg_summary.set("")
        elif lst:
            txt = "Ділянок: %d, разом %s із %s." % (len(lst), fmt_time(total), fmt_time(dur))
            if split_mode.get() != "manual":
                txt += " (Щоб їх застосувати, виберіть «Вручну».)"
            if problems:
                txt += "\n⚠ " + "; ".join(problems[:3])
            seg_summary.set(txt)
        else:
            seg_summary.set("Тривалість файлу: %s. Ділянок ще немає — додайте вручну, вставте трекліст "
                            "або заповніть автоматично." % fmt_time(dur))

    def materialize_ends(lst, dur):
        """Для ручних ділянок «Кінець» записуємо явно (щоб частини не перекривались)."""
        for i, sg in enumerate(lst):
            if sg[1] is None:
                sg[1] = lst[i + 1][0] if i + 1 < len(lst) else None
        return lst

    def set_manual():
        ed = cur_edits()
        if ed and ed["split"]["mode"] != "manual":
            ed["split"]["mode"] = "manual"
            split_mode.set("manual")
        refresh_compat_soon()

    def read_seg_fields():
        try:
            st = parse_time(seg_start.get())
            en = parse_time(seg_end.get())
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e))
            return None
        if st is None:
            messagebox.showerror(APP_NAME, "Вкажіть «Початок» ділянки (наприклад, 0:00 або 1:15:30).")
            return None
        dur = cur_duration()
        if dur and st >= dur:
            messagebox.showerror(APP_NAME, "Початок %s — після кінця файлу (%s)." % (fmt_ts(st), fmt_time(dur)))
            return None
        if en is not None and en <= st:
            messagebox.showerror(APP_NAME, "«Кінець» має бути пізніше за «Початок».")
            return None
        if en is not None and dur and en > dur:
            en = None
        return [st, en, seg_name.get().strip()]

    def seg_add():
        if not cur_edits():
            return
        sg = read_seg_fields()
        if not sg:
            return
        lst = segs()
        lst.append(sg)
        normalize_segs(lst)
        set_manual()
        render_segs(select=lst.index(sg))
        # підказка для наступної ділянки: початок = кінець цієї
        seg_start.set(fmt_ts(sg[1]) if sg[1] is not None else "")
        seg_end.set("")
        seg_name.set("")

    def seg_update():
        sel = seg_tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Виберіть ділянку у списку.")
            return
        sg = read_seg_fields()
        if not sg:
            return
        lst = segs()
        lst[int(sel[0])] = sg
        normalize_segs(lst)
        set_manual()
        render_segs(select=lst.index(sg))

    def seg_delete():
        lst = segs()
        for iid in sorted((int(x) for x in seg_tree.selection()), reverse=True):
            del lst[iid]
        render_segs()
        refresh_compat_soon()

    def seg_clear():
        ed = cur_edits()
        if not ed or not ed["split"]["segs"]:
            return
        if messagebox.askyesno(APP_NAME, "Видалити всі ділянки цього файлу?"):
            ed["split"]["segs"].clear()
            if ed["split"]["mode"] == "manual":
                ed["split"]["mode"] = "global"
                split_mode.set("global")
            render_segs()
            refresh_compat_soon()

    def on_seg_select(_e=None):
        sel = seg_tree.selection()
        if not sel:
            return
        st, en, name = segs()[int(sel[0])]
        seg_start.set(fmt_ts(st))
        seg_end.set(fmt_ts(en) if en is not None else "")
        seg_name.set(name)

    seg_tree.bind("<<TreeviewSelect>>", on_seg_select)
    seg_tree.bind("<Delete>", lambda e: seg_delete())

    def replace_segs(new, what):
        ed = cur_edits()
        if not ed:
            return
        if ed["split"]["segs"] and not messagebox.askyesno(
                APP_NAME, "Замінити наявні ділянки (%d) на %s?" % (len(ed["split"]["segs"]), what)):
            return
        ed["split"]["segs"] = materialize_ends(normalize_segs(new), cur_duration())
        set_manual()
        render_segs()

    auto_min = tk.StringVar(value=str(cfg.get("auto_min", "10")))
    auto_cnt = tk.StringVar(value="4")

    def fill_by_minutes():
        try:
            m = float(auto_min.get().replace(",", "."))
        except ValueError:
            m = 0
        if m <= 0:
            messagebox.showerror(APP_NAME, "Вкажіть тривалість частини у хвилинах, наприклад 10.")
            return
        cfg["auto_min"] = auto_min.get()
        new = auto_segments(cur_duration(), m)
        replace_segs(new, "%s по %g хв" % (plural(len(new), "ділянку", "ділянки", "ділянок"), m))

    def fill_equal():
        try:
            n = int(auto_cnt.get())
        except ValueError:
            n = 0
        if n < 2 or n > 200:
            messagebox.showerror(APP_NAME, "Кількість частин — від 2 до 200.")
            return
        replace_segs(equal_segments(cur_duration(), n), plural(n, "рівну частину", "рівні частини", "рівних частин"))

    def paste_tracklist():
        if not cur_edits():
            return
        dlg = tk.Toplevel(root)
        dlg.title("Вставити трекліст")
        dlg.transient(root)
        dlg.geometry("560x430")
        ttk.Label(dlg, text="Вставте список (наприклад, з опису відео або сайту). У кожному рядку — час "
                            "початку і назва, у будь-якому порядку:", wraplength=530, justify="left").pack(
            anchor="w", padx=10, pady=(10, 2))
        ttk.Label(dlg, text="0:00 Linkin Park - Numb\n3:05 Nickelback - How You Remind Me\n"
                            "1:02:30 The Killers - Mr. Brightside", foreground="#607d8b",
                  font=("Consolas", 9), justify="left").pack(anchor="w", padx=16)
        txt = tk.Text(dlg, height=12, wrap="none", font=("Consolas", 10))
        txt.pack(fill="both", expand=True, padx=10, pady=6)
        try:
            clip = root.clipboard_get()
            if _TS_IN_LINE.search(clip or ""):
                txt.insert("1.0", clip)
        except tk.TclError:
            pass
        res = tk.StringVar()
        ttk.Label(dlg, textvariable=res, foreground="#37474f").pack(anchor="w", padx=10)
        bb = ttk.Frame(dlg)
        bb.pack(fill="x", padx=10, pady=(4, 10))

        def check():
            found = parse_tracklist(txt.get("1.0", "end"), cur_duration())
            res.set("Розпізнано ділянок: %d" % len(found) + (
                " (перша — %s «%s»)" % (fmt_ts(found[0][0]), found[0][2]) if found else ""))
            return found

        def apply():
            found = check()
            if not found:
                messagebox.showerror(APP_NAME, "Не знайдено жодного рядка з часом.", parent=dlg)
                return
            if found[0][0] > 1:
                found.insert(0, [0.0, found[0][0], "Вступ"])
                if not messagebox.askyesno(APP_NAME, "Перша позиція починається не з 0:00. Додати "
                                                     "ділянку «Вступ» від 0:00?", parent=dlg):
                    found.pop(0)
            dlg.destroy()
            if any(" - " in f[2] for f in found):
                seg_artist.set(True)
                on_seg_artist()
            replace_segs(found, "%s із треклісту" % plural(len(found), "ділянку", "ділянки", "ділянок"))

        ttk.Button(bb, text="Перевірити", command=check).pack(side="left")
        ttk.Button(bb, text="Застосувати", command=apply).pack(side="right")
        ttk.Button(bb, text="Скасувати", command=dlg.destroy).pack(side="right", padx=6)
        txt.bind("<KeyRelease>", lambda e: check())
        check()
        dlg.grab_set()
        txt.focus_set()

    for text, cmd in (("Додати", seg_add), ("Змінити вибрану", seg_update),
                      ("Видалити", seg_delete), ("Очистити всі", seg_clear)):
        ttk.Button(seg_btns, text=text, command=cmd).pack(side="left", padx=(0, 4))
    ttk.Label(tools, text="Заповнити: кожні").pack(side="left")
    ttk.Spinbox(tools, textvariable=auto_min, from_=1, to=120, increment=5, width=5).pack(side="left", padx=2)
    ttk.Button(tools, text="хв", width=4, command=fill_by_minutes).pack(side="left")
    ttk.Label(tools, text="  або на").pack(side="left")
    ttk.Spinbox(tools, textvariable=auto_cnt, from_=2, to=200, width=4).pack(side="left", padx=2)
    ttk.Button(tools, text="рівних частин", command=fill_equal).pack(side="left")
    ttk.Button(tools, text="Вставити трекліст…", command=paste_tracklist).pack(side="right")

    # Прослуховування — через програвач угорі (з гучністю й еквалайзером)
    def stop_play():
        if pl["playing"]:
            pl["pos"] = player_pos()
            pl["playing"] = False
            kill_proc()
            update_player_ui()

    def play_at(which):
        path = cur["path"]
        if not path:
            return
        try:
            st = parse_time(seg_start.get()) or 0.0
            en = parse_time(seg_end.get())
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        if which == "end":
            if en is None:
                messagebox.showinfo(APP_NAME, "Заповніть поле «Кінець».")
                return
            st = max(0.0, en - 8)
        player_load(path, play=True, pos=st)

    ttk.Label(listen, text="Прослухати:").pack(side="left")
    ttk.Button(listen, text="▶ від «Початку»", command=lambda: play_at("start")).pack(side="left", padx=2)
    ttk.Button(listen, text="▶ перед «Кінцем»", command=lambda: play_at("end")).pack(side="left", padx=2)
    ttk.Button(listen, text="■ Стоп", command=stop_play).pack(side="left", padx=2)

    def on_split_mode():
        ed = cur_edits()
        if cur["loading"] or not ed:
            return
        mode = split_mode.get()
        if mode == "manual" and not ed["split"]["segs"]:
            messagebox.showinfo(APP_NAME, "Додайте ділянки: вручну (Початок/Кінець → «Додати»), "
                                          "кнопкою «Вставити трекліст…» або «Заповнити».")
        ed["split"]["mode"] = mode
        render_segs()
        refresh_compat_soon()

    def on_seg_artist():
        ed = cur_edits()
        if ed:
            ed["split"]["artist_title"] = bool(seg_artist.get())

    def update_global_split_label(*_):
        try:
            m = float(v["split"].get().replace(",", "."))
        except (ValueError, NameError):
            m = 0
        sm_global.configure(text="Як у загальних налаштуваннях: " + (
            "по %g хв" % m if m > 0 else "не розбивати") + " (поле праворуч)")

    def set_editors_state(enabled):
        st = "normal" if enabled else "disabled"
        for w in tag_widgets:
            w.configure(state=st)
        for frame in (tbtns, cov_btn_row, seg_btns, tools, listen, sm_frame, ed_row):
            for w in frame.winfo_children():
                try:
                    w.configure(state=st)
                except tk.TclError:
                    pass
        for rb in cov_radios.values():
            rb.configure(state=st)

    def load_editors(path):
        """Показати налаштування вибраного файлу у вкладках."""
        cur["path"] = path if path in state["edits"] else None
        ed = cur_edits()
        cur["loading"] = True
        try:
            for k, var in tag_vars.items():
                var.set(ed["tags"].get(k, "") if ed else "")
            cover_mode.set(ed["cover"]["mode"] if ed else "global")
            info = state["info"].get(path)
            has_cov = isinstance(info, dict) and info.get("cover")
            cov_radios["source"].configure(text="Залишити обкладинку з файлу" + ("" if has_cov else " (у файлі немає)"))
            split_mode.set(ed["split"]["mode"] if ed else "global")
            seg_artist.set(ed["split"].get("artist_title", False) if ed else False)
            seg_start.set("")
            seg_end.set("")
            seg_name.set("")
        finally:
            cur["loading"] = False
        set_editors_state(bool(ed))
        if ed and not has_cov:
            cov_radios["source"].configure(state="disabled")
        update_global_split_label()
        render_segs()
        update_cover_view()

    def on_select(_e=None):
        p = flist.focus() or None
        show_info(p)
        load_editors(p)
        # якщо нічого не грає — програвач «бере» вибраний файл
        if p and not pl["playing"] and isinstance(state["info"].get(p), dict) and pl["path"] != p:
            player_load(p)

    def on_double(event):
        p = flist.identify_row(event.y)
        if p and isinstance(state["info"].get(p), dict):
            player_load(p, play=True, pos=0.0)

    flist.bind("<<TreeviewSelect>>", on_select)
    flist.bind("<Double-1>", on_double)
    flist.bind("<Return>", lambda e: flist.focus() and player_load(flist.focus(), play=True, pos=0.0))

    # Праворуч: параметри вихідного файлу
    out_box = ttk.LabelFrame(mid, text=" Параметри вихідного файлу ", padding=8)
    out_box.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
    out_box.columnconfigure(1, weight=1)

    v = {k: tk.StringVar(value=str(settings[k])) for k in
         ("format", "mode", "bitrate", "vbr_level", "rate", "channels", "bits", "id3", "split")}
    b = {k: tk.BooleanVar(value=bool(settings[k])) for k in
         ("keep_tags", "keep_cover", "normalize", "id3_only23", "id3v1_cp1251", "cover_resize", "split_folder")}

    row = 0

    def add_row(label, widget, hint=None):
        nonlocal row
        ttk.Label(out_box, text=label).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 8))
        widget.grid(row=row, column=1, sticky="we", pady=3)
        if hint:
            ttk.Label(out_box, text=hint, foreground="#607d8b").grid(row=row, column=2, sticky="w", padx=6)
        row += 1
        return widget

    cb_format = add_row("Формат:", ttk.Combobox(out_box, textvariable=v["format"],
                                                values=list(FORMATS), state="readonly"))
    cb_mode = add_row("Тип бітрейту:", ttk.Combobox(out_box, textvariable=v["mode"], state="readonly",
                                                    values=["CBR (постійний)", "VBR (змінний)"]),
                      "CBR — найсумісніше")
    cb_br = add_row("Бітрейт, кбіт/с:", ttk.Combobox(out_box, textvariable=v["bitrate"], state="readonly"))
    cb_vbr = add_row("Якість VBR:", ttk.Combobox(out_box, textvariable=v["vbr_level"], state="readonly",
                                                 values=MP3_VBR_LEVELS))
    cb_rate = add_row("Частота, Гц:", ttk.Combobox(out_box, textvariable=v["rate"], state="readonly"),
                      "44100 — стандарт CD")
    add_row("Канали:", ttk.Combobox(out_box, textvariable=v["channels"], state="readonly",
                                    values=["Як у вихідному", "Стерео", "Моно"]))
    cb_bits = add_row("Розрядність, біт:", ttk.Combobox(out_box, textvariable=v["bits"], state="readonly",
                                                        values=["16", "24"]), "для FLAC/WAV")
    cb_id3 = add_row("Теги MP3:", ttk.Combobox(out_box, textvariable=v["id3"], state="readonly",
                                               values=["ID3v2.3 + ID3v1", "ID3v2.3", "ID3v2.4"]),
                     "v2.3 — найсумісніше")
    chk_23 = ttk.Checkbutton(out_box, text="Лише ID3v2.3 (без ID3v2.4) — для старих магнітол",
                             variable=b["id3_only23"])
    chk_23.grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 2))
    row += 1
    chk_1251 = ttk.Checkbutton(out_box, text="ID3v1 — кирилиця у Windows-1251 (без «кракозябр»)",
                               variable=b["id3v1_cp1251"])
    chk_1251.grid(row=row, column=0, columnspan=3, sticky="w", pady=2)
    row += 1
    add_row("Розбити на частини по, хв:", ttk.Spinbox(out_box, textvariable=v["split"], from_=0, to=120,
                                                      increment=5, width=8), "0 — не розбивати")
    ttk.Label(out_box, text="Для окремого файлу (вручну, за треклістом) — вкладка «Розбиття на ділянки».",
              foreground="#607d8b").grid(row=row, column=0, columnspan=3, sticky="w")
    row += 1
    checks = ttk.Frame(out_box)
    checks.grid(row=row, column=0, columnspan=3, sticky="we", pady=(4, 0))
    row += 1
    for key, text, r, c in (
            ("keep_tags", "Зберегти теги (виконавець, назва, альбом…)", 0, 0),
            ("split_folder", "Частини файлу — в окрему папку", 0, 1),
            ("keep_cover", "Зберегти обкладинку альбому", 1, 0),
            ("cover_resize", "Обкладинку з файлу — JPEG до %d×%d" % (COVER_MAX, COVER_MAX), 1, 1),
            ("normalize", "Вирівняти гучність (EBU R128, −14 LUFS)", 2, 0)):
        ttk.Checkbutton(checks, text=text, variable=b[key]).grid(row=r, column=c, sticky="w",
                                                                 pady=2, padx=(0, 12))

    preset_bar = ttk.Frame(out_box)
    preset_bar.grid(row=row, column=0, columnspan=3, sticky="we", pady=(10, 2))
    row += 1
    est_var = tk.StringVar()
    ttk.Label(out_box, textvariable=est_var, foreground="#37474f").grid(
        row=row, column=0, columnspan=3, sticky="w", pady=(6, 0))
    row += 1

    def refresh_controls(*_):
        fmt = FORMATS.get(v["format"].get(), FORMATS["MP3"])
        cb_rate.configure(values=["Як у вихідному"] + fmt["rates"])
        if v["rate"].get() not in cb_rate["values"]:
            v["rate"].set("44100")
        if fmt["lossless"]:
            cb_br.configure(values=[], state="disabled")
            cb_mode.configure(state="disabled")
            cb_vbr.configure(state="disabled")
            cb_bits.configure(state="readonly")
        else:
            cb_bits.configure(state="disabled")
            cb_br.configure(values=fmt["bitrates"])
            if v["bitrate"].get() not in fmt["bitrates"]:
                v["bitrate"].set(fmt["default_br"])
            if fmt["vbr"]:
                cb_mode.configure(state="readonly")
            else:
                v["mode"].set("CBR (постійний)")
                cb_mode.configure(state="disabled")
            is_vbr = v["mode"].get().startswith("VBR") and fmt["vbr"]
            cb_br.configure(state="disabled" if is_vbr else "readonly")
            cb_vbr.configure(state="readonly" if is_vbr else "disabled")
        is_mp3 = v["format"].get() == "MP3"
        if b["id3_only23"].get():
            cb_id3.configure(values=["ID3v2.3 + ID3v1", "ID3v2.3"])
            if v["id3"].get() == "ID3v2.4":
                v["id3"].set("ID3v2.3 + ID3v1")
        else:
            cb_id3.configure(values=["ID3v2.3 + ID3v1", "ID3v2.3", "ID3v2.4"])
        cb_id3.configure(state="readonly" if is_mp3 else "disabled")
        chk_23.configure(state="normal" if is_mp3 else "disabled")
        chk_1251.configure(state="normal" if is_mp3 and "ID3v1" in v["id3"].get() else "disabled")
        update_estimate()
        if cur.get("path"):
            update_cover_view()
            refresh_compat_soon()

    def current_settings():
        s = {k: var.get() for k, var in v.items()}
        s.update({k: var.get() for k, var in b.items()})
        s["eq_gains"] = [round(g.get(), 1) for g in eq_gains]
        s["eq_apply"] = bool(eq_apply.get())
        s["eq_preset"] = eq_preset.get()
        s["volume"] = round(vol.get())
        return s

    def update_estimate(*_):
        total = sum(i["duration"] for i in state["info"].values() if isinstance(i, dict))
        if not total:
            est_var.set("⚠ Еквалайзер буде застосовано до нових файлів." if eq_apply.get() else "")
            return
        s = current_settings()
        fmt = FORMATS[s["format"]]
        if fmt["lossless"]:
            sr = int(s["rate"]) if s["rate"].isdigit() else 44100
            ch = 1 if s["channels"] == "Моно" else 2
            kbps = sr * ch * int(s["bits"]) / 1000 * (0.6 if s["format"] == "FLAC" else 1)
        elif s["format"] == "MP3" and s["mode"].startswith("VBR"):
            kbps = int(re.search(r"(\d+)\)", s["vbr_level"]).group(1))
        else:
            kbps = int(s["bitrate"])
        txt = ("Орієнтовний розмір результату: %s (усі файли у списку, %s)"
               % (fmt_size(total * kbps * 125), fmt_time(total)))
        if s.get("eq_apply") and eq_filters(s.get("eq_gains")):
            txt += "\n⚠ Еквалайзер «%s» буде застосовано до нових файлів." % s.get("eq_preset", "")
        est_var.set(txt)

    def apply_preset(p):
        for k, val in p.items():
            if k in v:
                v[k].set(str(val))
            elif k in b:
                b[k].set(bool(val))
        refresh_controls()

    def copy_from_reference():
        if not state["ffprobe"] and not locate_ffmpeg(True):
            return
        path = filedialog.askopenfilename(
            title="Виберіть файл, який автомагнітола ВІДТВОРЮЄ",
            filetypes=[("Аудіофайли", " ".join("*" + e for e in AUDIO_EXT)), ("Усі файли", "*.*")])
        if not path:
            return
        try:
            ref = probe_file(state["ffprobe"], path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(APP_NAME, str(e))
            return
        codec_map = {"mp3": "MP3", "aac": "AAC (M4A)", "wmav2": "WMA", "vorbis": "OGG Vorbis",
                     "flac": "FLAC"}
        fmt_name = codec_map.get(ref.get("codec"), "MP3")
        p = {"format": fmt_name}
        if ref.get("sample_rate"):
            p["rate"] = str(ref["sample_rate"])
        p["channels"] = "Моно" if ref.get("channels") == 1 else "Стерео"
        mp3 = ref.get("mp3", {})
        br = round((ref.get("bitrate") or ref.get("overall_bitrate") or 192000) / 1000)
        choices = [int(x) for x in FORMATS[fmt_name]["bitrates"]] or [0]
        p["bitrate"] = str(min(choices, key=lambda c: abs(c - br)))
        if fmt_name == "MP3":
            p["mode"] = "VBR (змінний)" if mp3.get("brmode", "").startswith("VBR") else "CBR (постійний)"
            if mp3.get("id3v2") == "ID3v2.4":
                p["id3_only23"] = False  # магнітола читає v2.4 — обмеження не потрібне
                p["id3"] = "ID3v2.4"
            elif mp3.get("id3v2"):
                p["id3"] = "ID3v2.3 + ID3v1" if mp3.get("id3v1") else "ID3v2.3"
        apply_preset(p)
        lines = ["Параметри взято з «%s»:" % os.path.basename(path)]
        lines += ["• %s: %s" % (k, val) for k, val in describe(ref)[4:12]]
        messagebox.showinfo(APP_NAME, "\n".join(lines))

    ttk.Button(preset_bar, text="★ Профіль «Автомагнітола»",
               command=lambda: apply_preset(CAR_PRESET)).pack(side="left")
    ttk.Button(preset_bar, text="Взяти параметри з файлу, що грає…",
               command=copy_from_reference).pack(side="left", padx=6)

    v["format"].trace_add("write", refresh_controls)
    v["mode"].trace_add("write", refresh_controls)
    v["id3"].trace_add("write", refresh_controls)
    for k in ("id3_only23", "keep_cover", "cover_resize"):
        b[k].trace_add("write", refresh_controls)

    def on_global_split(*_):
        update_global_split_label()
        if cur.get("path"):
            render_segs()
            refresh_compat_soon()
    v["split"].trace_add("write", on_global_split)
    for k in ("bitrate", "vbr_level", "rate", "channels", "bits"):
        v[k].trace_add("write", update_estimate)

    # ---------- Нижня частина ----------
    bottom = ttk.Frame(root, padding=(10, 8, 10, 10))
    bottom.pack(fill="x", side="bottom", before=mid)
    bottom.columnconfigure(1, weight=1)

    ttk.Label(bottom, text="Зберегти в папку:").grid(row=0, column=0, sticky="w")
    out_dir = tk.StringVar(value=cfg.get("out_dir", ""))
    ttk.Entry(bottom, textvariable=out_dir).grid(row=0, column=1, sticky="we", padx=6)

    def choose_out():
        d = filedialog.askdirectory(title="Куди зберігати перекодовані файли",
                                    initialdir=out_dir.get() or cfg.get("last_in_dir") or None)
        if d:
            out_dir.set(d)

    ttk.Button(bottom, text="Огляд…", command=choose_out).grid(row=0, column=2)
    ttk.Label(bottom, text="(порожньо — поруч із вихідним файлом, з додатком «_car»)",
              foreground="#607d8b").grid(row=1, column=1, sticky="w", padx=6)

    prog_file = ttk.Progressbar(bottom, maximum=1000)
    prog_file.grid(row=2, column=0, columnspan=3, sticky="we", pady=(8, 2))
    prog_all = ttk.Progressbar(bottom, maximum=1000)
    prog_all.grid(row=3, column=0, columnspan=3, sticky="we", pady=2)
    status = tk.StringVar(value="Додайте файли для перекодування.")
    ttk.Label(bottom, textvariable=status).grid(row=4, column=0, columnspan=3, sticky="w")

    actions = ttk.Frame(bottom)
    actions.grid(row=5, column=0, columnspan=3, sticky="we", pady=(8, 0))
    ff_var = tk.StringVar()
    ff_label = ttk.Label(actions, textvariable=ff_var)
    ff_label.pack(side="left")
    ttk.Button(actions, text="Вказати FFmpeg…", command=choose_ffmpeg).pack(side="left", padx=8)
    btn_stop = ttk.Button(actions, text="Зупинити", state="disabled",
                          command=lambda: state["stop"].set())
    btn_stop.pack(side="right")
    btn_open = ttk.Button(actions, text="Відкрити папку результату", state="disabled")
    btn_open.pack(side="right", padx=6)
    btn_go = ttk.Button(actions, text="▶ Перекодувати", style="Big.TButton")
    btn_go.pack(side="right", padx=6)

    last_out = {"dir": None}

    def open_out():
        d = last_out["dir"]
        if d and os.path.isdir(d):
            if os.name == "nt":
                os.startfile(d)  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", d])

    btn_open.configure(command=open_out)

    # ---------- Перекодування ----------
    def start():
        if state["worker"] and state["worker"].is_alive():
            return
        if not state["ffmpeg"] and not locate_ffmpeg(True):
            return
        targets = list(state["files"])  # перекодовуються всі файли зі списку
        if not targets:
            messagebox.showinfo(APP_NAME, "Спершу додайте файли.")
            return
        missing = [t for t in targets if not isinstance(state["info"].get(t), dict)]
        if missing:
            messagebox.showinfo(APP_NAME, "Зачекайте, поки завершиться аналіз файлів "
                                          "(або видаліть файли з помилкою).")
            return
        d = out_dir.get().strip()
        if d and not os.path.isdir(d):
            try:
                os.makedirs(d)
            except OSError as e:
                messagebox.showerror(APP_NAME, "Не вдалося створити папку:\n%s" % e)
                return
        s = current_settings()
        # перевірка ручних ділянок
        empty_manual = [os.path.basename(t) for t in targets
                        if state["edits"].get(t, {}).get("split", {}).get("mode") == "manual"
                        and not state["edits"][t]["split"]["segs"]]
        if empty_manual:
            if not messagebox.askyesno(APP_NAME, "Для файлів вибрано ручне розбиття, але ділянок немає:\n\n"
                                       + "\n".join(empty_manual[:5])
                                       + "\n\nПерекодувати їх цілими?"):
                return
        n_parts = sum(len(plan_jobs(state["info"][t], s, state["edits"].get(t, {}).get("split")))
                      for t in targets)
        cfg["settings"] = s
        cfg["out_dir"] = d
        save_config(cfg)
        state["stop"].clear()
        btn_go.configure(state="disabled")
        btn_stop.configure(state="normal")
        edits = {t: copy.deepcopy(state["edits"].get(t)) for t in targets}
        if n_parts > len(targets):
            status.set("Буде створено файлів: %d (з урахуванням розбиття)." % n_parts)
        state["worker"] = threading.Thread(target=worker, args=(targets, s, d, edits), daemon=True)
        state["worker"].start()

    def finish_mp3(dst, s):
        """Після FFmpeg: ID3v1 у Windows-1251 і перевірка версії тегів."""
        if "ID3v1" in s["id3"] and s.get("id3v1_cp1251"):
            write_id3v1_cp1251(dst, read_out_tags(state["ffprobe"], dst))
        return mp3_header_info(dst).get("id3v2", "")

    def worker(targets, s, d, edits):
        total = sum(state["info"][t]["duration"] for t in targets) or 1.0
        done = 0.0
        ok_count = 0
        errors = []
        for path in targets:
            if state["stop"].is_set():
                break
            info = state["info"][path]
            target_dir = d or os.path.dirname(path)
            ed = edits.get(path) or {}
            jobs = plan_jobs(info, s, ed.get("split"))
            ui_queue.put(("status_row", path, "перекодування…", ""))
            failed = None
            outs = []
            tag_ver = ""
            for start_t, length, part, parts, name in jobs:
                if state["stop"].is_set():
                    break
                dst = output_name(path, target_dir, s, part, parts, name)
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                except OSError as e:
                    failed = "Не вдалося створити папку: %s" % e
                    break
                outs.append(dst)
                seg = length if length is not None else info["duration"]
                label = os.path.basename(path) + ("  (частина %d з %d)" % (part, parts) if part else "")
                ui_queue.put(("status", "Перекодування: " + label))
                cmd = build_command(state["ffmpeg"], path, dst, s, info, start_t, length, part, parts,
                                    name, ed)
                base_done = done

                def prog(fr, base=base_done, seg=seg):
                    ui_queue.put(("progress", fr, (base + fr * seg) / total))

                code, err = run_ffmpeg(cmd, seg, prog, state["stop"])
                done = base_done + seg
                if state["stop"].is_set():
                    try:
                        os.remove(dst)
                    except OSError:
                        pass
                    break
                if code != 0:
                    failed = err or "FFmpeg завершився з кодом %d" % code
                    break
                if s["format"] == "MP3":
                    tag_ver = finish_mp3(dst, s) or tag_ver
            if state["stop"].is_set():
                ui_queue.put(("status_row", path, "зупинено", "warn"))
                break
            if failed:
                errors.append((path, failed))
                ui_queue.put(("status_row", path, "помилка перекодування", "error"))
            else:
                ok_count += 1
                last_out["dir"] = os.path.dirname(outs[0])
                txt = "готово → %s" % os.path.basename(outs[0])
                if len(outs) > 1:
                    txt = "готово → " + plural(len(outs), "частина", "частини", "частин")
                if tag_ver:
                    txt += " (%s)" % tag_ver
                ui_queue.put(("status_row", path, txt, "ok"))
        ui_queue.put(("finished", ok_count, len(targets), errors, state["stop"].is_set()))

    btn_go.configure(command=start)

    # ---------- Обробка подій з потоків ----------
    def pump():
        try:
            while True:
                msg = ui_queue.get_nowait()
                kind = msg[0]
                if kind == "probed":
                    _, path, info = msg
                    if path not in state["files"]:
                        continue
                    state["info"][path] = info
                    if path not in state["edits"]:
                        state["edits"][path] = new_edits(info)
                    issues = compat_issues(info, state["edits"][path], current_settings())
                    worst = "ok"
                    for lvl in ("error", "warn"):
                        if any(i[0] == lvl for i in issues):
                            worst = lvl
                            break
                    text = {"ok": "✔ сумісний",
                            "warn": "⚠ можливі проблеми",
                            "error": "✖ найімовірніше не гратиме"}[worst]
                    br = info.get("bitrate") or info.get("overall_bitrate")
                    flist.item(path, values=(
                        os.path.basename(path), (info.get("codec") or "—").upper(),
                        "%d Гц" % info.get("sample_rate", 0) if info.get("sample_rate") else "—",
                        "%d кбіт/с" % round(br / 1000) if br else "—",
                        fmt_time(info["duration"]), text), tags=(worst,))
                    if flist.focus() == path:
                        show_info(path)
                        load_editors(path)
                        if not pl["playing"] and pl["path"] != path:
                            player_load(path)
                    update_estimate()
                elif kind == "thumb":
                    _, key, png, size = msg
                    if not png:
                        thumbs["cache"].pop(key, None)
                    if thumbs["want"] == key:
                        if png:
                            show_thumb(png)
                            if size and not key[1]:
                                note = cov_info.get().replace(
                                    os.path.basename(key[0]), "%s (%s)" % (os.path.basename(key[0]), size), 1)
                                m = re.match(r"(\d+)x(\d+)", size)
                                if m:
                                    w, h = int(m.group(1)), int(m.group(2))
                                    w2, h2 = fit_cover(w, h)
                                    lines = note.split("\n")
                                    lines = [ln for ln in lines if not ln.startswith("У результаті")]
                                    if (w2, h2) != (w, h):
                                        lines.append("У результаті: JPEG %d×%d — зменшено пропорційно "
                                                     "(було %d×%d)." % (w2, h2, w, h))
                                    else:
                                        lines.append("У результаті: JPEG %d×%d — розмір не змінюється." % (w, h))
                                    note = "\n".join(lines)
                                cov_info.set(note)
                        else:
                            cov_img.configure(image="", text="не вдалося\nпрочитати")
                elif kind == "probe_err":
                    _, path, err = msg
                    if path in state["files"]:
                        state["info"][path] = err
                        flist.item(path, values=(os.path.basename(path), "—", "", "", "",
                                                 "✖ не вдалося прочитати"), tags=("error",))
                        if flist.focus() == path:
                            show_info(path)
                elif kind == "status":
                    status.set(msg[1])
                elif kind == "status_row":
                    _, path, text, tag = msg
                    if flist.exists(path):
                        vals = list(flist.item(path, "values"))
                        vals[5] = text
                        flist.item(path, values=vals, tags=(tag,) if tag else ())
                elif kind == "progress":
                    prog_file["value"] = msg[1] * 1000
                    prog_all["value"] = msg[2] * 1000
                elif kind == "finished":
                    _, ok_count, n, errors, stopped = msg
                    btn_go.configure(state="normal")
                    btn_stop.configure(state="disabled")
                    if last_out["dir"]:
                        btn_open.configure(state="normal")
                    prog_file["value"] = 0 if stopped else 1000
                    prog_all["value"] = 0 if stopped else 1000
                    if stopped:
                        status.set("Зупинено. Готово файлів: %d з %d." % (ok_count, n))
                    else:
                        status.set("Готово: %d з %d файлів." % (ok_count, n))
                    if errors:
                        messagebox.showerror(APP_NAME, "Помилки:\n\n" + "\n\n".join(
                            "%s:\n%s" % (os.path.basename(p), e[-600:]) for p, e in errors[:3]))
        except queue.Empty:
            pass
        root.after(100, pump)

    def on_close():
        if state["worker"] and state["worker"].is_alive():
            if not messagebox.askyesno(APP_NAME, "Перекодування ще триває. Зупинити й вийти?"):
                return
            state["stop"].set()
        kill_proc()
        cfg["settings"] = current_settings()
        cfg["out_dir"] = out_dir.get().strip()
        save_config(cfg)
        if thumbs["dir"]:
            shutil.rmtree(thumbs["dir"], ignore_errors=True)
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    refresh_controls()
    load_editors(None)
    locate_ffmpeg()
    if len(sys.argv) > 1:  # файли, передані через «Відкрити за допомогою» / перетягування на ярлик
        root.after(200, lambda: add_paths([a for a in sys.argv[1:] if os.path.isfile(a)]))
    elif not state["ffmpeg"]:
        root.after(400, lambda: locate_ffmpeg(True))
    root.after(100, pump)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
