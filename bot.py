"""
Telecomando PC via Telegram - pensato per letto/divano (YouTube, serie, ecc.)
Comandi tastiera + pulsanti. Solo gli ID in ALLOWED_IDS possono usarlo.
"""
import logging
import os
import re
import sys
import webbrowser
import subprocess
import urllib.parse
from dotenv import load_dotenv

# .env vicino all'exe (quando compilato) + cartella corrente
if getattr(sys, "frozen", False):
    _base = os.path.dirname(sys.executable)
else:
    _base = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_base, ".env"))
load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
ALLOWED_IDS = set(
    x.strip() for x in os.getenv("ALLOWED_IDS", "").split(",") if x.strip()
)
VOICE_MODEL = os.getenv("VOICE_MODEL", "base").strip() or "base"

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO
)
try:
    from logging.handlers import RotatingFileHandler
    _fh = RotatingFileHandler(
        os.path.join(_base, "bot.log"), maxBytes=1_000_000,
        backupCount=3, encoding="utf-8",
    )
    _fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logging.getLogger().addHandler(_fh)
except Exception:
    pass
log = logging.getLogger("pc-remote")

# --- azioni PC ---

def press(*keys):
    """Premi tasti via pyautogui (lazy import così il bot parte anche senza)."""
    import pyautogui
    pyautogui.FAILSAFE = False
    # pyautogui vuole nomi minuscoli: 'space', 'right', 'volumeup', ecc.
    if len(keys) == 1:
        pyautogui.press(keys[0])
    else:
        pyautogui.hotkey(*keys)

def set_volume_percent(pct: int):
    """Imposta volume assoluto 0-100 via pycaw (solo Windows)."""
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    vol = cast(interface, POINTER(IAudioEndpointVolume))
    vol.SetMasterVolumeLevelScalar(max(0, min(100, pct)) / 100.0, None)

def type_text(text: str):
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.write(text, interval=0.02)

def monitor_off():
    """Spegne solo il monitor (il PC resta attivo e il bot risponde).
    Usa il broadcast SC_MONITORPOWER: vale per qualsiasi monitor, ma il risveglio
    via tasto/mouse funziona solo se il driver lo supporta."""
    import ctypes
    ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
    # fallback: alcuni driver ignorano il broadcast; riprova con il handle della finestra attiva
    try:
        import pygetwindow as gw
        w = gw.getActiveWindow()
        if w is not None:
            ctypes.windll.user32.SendMessageW(int(w._hWnd), 0x0112, 0xF170, 2)
    except Exception:
        pass

def monitor_on():
    """Riaccende il monitor con un movimento mouse + tasto innocuo."""
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.moveRel(20, 0, duration=0.2)
    pyautogui.moveRel(-20, 0, duration=0.2)
    pyautogui.press("shift")

YT_NUMBERS = {
    "zero": 0, "dieci": 10, "venti": 20, "trenta": 30, "quaranta": 40,
    "cinquanta": 50, "sessanta": 60, "settanta": 70, "ottanta": 80,
    "novanta": 90, "cento": 100, "cento per cento": 100, "massimo": 100,
    "metà": 50, "meta": 50, "minimo": 0,
}

def parse_yt_volume(text: str):
    """Estrae 0-100 da 'volume youtube 50' o '... cinquanta'. None se assente."""
    m = re.search(r"(\d{1,3})", text)
    if m:
        return max(0, min(100, int(m.group(1))))
    tl = text.lower()
    for word, val in YT_NUMBERS.items():
        if re.search(rf"\b{re.escape(word)}\b", tl):
            return val
    return None

def set_youtube_volume(pct: int):
    """Imposta il volume del player (YouTube/Netflix usano frecce su/giù a step 5%).
    Clicca al centro per dare focus al player, ripristina play/pausa, azzera e risale."""
    import time
    import pyautogui
    import pygetwindow as gw
    pyautogui.FAILSAFE = False
    pct = max(0, min(100, int(pct)))
    try:
        w = gw.getActiveWindow()
        if w is not None and w.width > 0:
            pyautogui.click(w.left + w.width // 2, w.top + w.height // 2)
            time.sleep(0.3)
            pyautogui.press("space")  # annulla il toggle play/pausa del clic
            time.sleep(0.2)
    except Exception:
        pass
    for _ in range(25):  # azzera (step 5% -> 25 = sicuro da 100)
        pyautogui.press("down")
        time.sleep(0.02)
    for _ in range(round(pct / 5)):
        pyautogui.press("up")
        time.sleep(0.02)
    return pct

# --- comandi vocali (trascrizione offline con faster-whisper) ---

_whisper_model = None

def get_whisper():
    """Carica il modello una sola volta (al primo vocale, scarica ~150MB per 'base')."""
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel(VOICE_MODEL, device="cpu", compute_type="int8")
    return _whisper_model

def transcribe_voice(path: str) -> str:
    model = get_whisper()
    segments, _info = model.transcribe(path, language="it", beam_size=5)
    return " ".join(s.text for s in segments).strip()

def match_voice_command(text: str):
    """Restituisce (etichetta, funzione) oppure (None, None) se non capito."""
    t = text.lower().strip()
    has = lambda *words: any(w in t for w in words)

    if has("riavvia"):
        return "__CF:riavvia:1", lambda: None
    if has("pausa", "play", "riproduci", "avvia", "ferma il video", "metti in pausa", "continua"):
        return "⏯ Play/Pausa", lambda: press("space")
    if has("tutto schermo", "schermo intero", "fullscreen", "tutto lo schermo"):
        return "⛶ Fullscreen", lambda: press("f")
    if has("smuta", "togli l'audio del video", "muta il video"):
        return "Muta video", lambda: press("m")
    if ("youtube" in t or " yt " in f" {t} " or t.startswith("yt ")) and ("volume" in t or "volum" in t):
        pct = parse_yt_volume(t)
        if pct is not None:
            def _v(p=pct):
                set_youtube_volume(p)
            return f"🎚️ Volume YouTube → {pct}%", _v
        if has("alza", "aumenta", "più", "alto"):
            def _vu():
                import pyautogui
                pyautogui.FAILSAFE = False
                for _ in range(4):
                    pyautogui.press("up")
            return "🎚️ Volume YouTube +", _vu
        if has("abbassa", "basso", "meno", "diminuisci"):
            def _vd():
                import pyautogui
                pyautogui.FAILSAFE = False
                for _ in range(4):
                    pyautogui.press("down")
            return "🎚️ Volume YouTube −", _vd
    if has("alza il volume", "aumenta il volume", "volume più alto", "volume alto", "alza volume"):
        def up():
            for _ in range(6):
                press("volumeup")
        return "🔊 Volume +", up
    if has("abbassa il volume", "volume più basso", "volume basso", "abbassa volume", "diminuisci"):
        def down():
            for _ in range(6):
                press("volumedown")
        return "🔉 Volume −", down
    if has("muto", "silenzia", "togli l'audio", "senza audio"):
        return "🔇 Muta PC", lambda: press("volumemute")
    if has("avanti", "manda avanti"):
        return "⏩ +10s", lambda: press("right")
    if has("indietro", "torna indietro"):
        return "⏪ −10s", lambda: press("left")
    if has("successiv", "prossim", "cambia episodio", "episodio successivo"):
        return "⏭ Next", lambda: press("medianext")
    if has("precedente"):
        return "⏮ Prev", lambda: press("mediaprev")
    me = re.match(r"^(?:nuova scheda|apri (?:una )?nuova scheda|scheda nuova)\s*(.*)$", t)
    if me:
        dest = me.group(1).strip()
        def _new(d=dest):
            import time
            press("ctrl", "t")
            time.sleep(0.6)
            if d:
                type_text(d)
                time.sleep(0.3)
                press("enter")
        return ("📑 Nuova scheda" + (f" → {dest}" if dest else ""), _new)
    if has("riapri la scheda", "riapri scheda", "riapri ultima scheda", "riapri quella chiusa"):
        return "📑 Riapro scheda", lambda: press("ctrl", "shift", "t")
    me = re.match(r"^chiudi\s+(.+)$", t)
    if me:
        query = me.group(1).strip()
        if query and query not in ("tab", "scheda", "la scheda"):
            def _close(q=query):
                if close_window(q) is None:
                    raise RuntimeError(f'nessuna finestra "{q}"')
            return f"✖ Chiudo: {query}", _close
    if has("chiudi"):
        return "✖ Chiudi tab", lambda: press("ctrl", "w")
    if has("schermo spento", "spegni lo schermo", "schermo off", "spegni il monitor"):
        return "🖥️ Schermo OFF", lambda: monitor_off()
    if has("schermo acceso", "accendi lo schermo", "schermo on", "riaccendi il monitor"):
        return "🖥️ Schermo ON", lambda: monitor_on()
    if has("blocca il computer", "blocca il pc", "sospendi", "metti in sospensione", "standby"):
        return "__CF:sospendi:", lambda: None
    if has("spegni"):
        return "__CF:spegni:1", lambda: None
    if has("screenshot", "foto dello schermo", "foto allo schermo", "mandami una foto", "fammi vedere lo schermo"):
        return "__SCREENSHOT__", lambda: None
    me = re.match(r"^(?:scrivi|digita|detta)\s+(.+)$", t)
    if me:
        txt = me.group(1).strip()
        if txt:
            def _type(q=txt):
                type_text(q)
            return f"⌨️ Scrivo: {txt}", _type
    me = re.match(r"^(?:open\s?code|opencode)\s+(.+)$", t)
    if me:
        txt = me.group(1).strip()
        if txt:
            def _req(q=txt):
                salva_richiesta(q, origine="vocale")
            return f"📝 Richiesta salvata: {txt}", _req
    me = re.match(r"^(?:vai su|passa a|portami (?:su|a)|cambia (?:a|app)|finestra)\s+(.+)$", t)
    if me:
        query = me.group(1).strip()
        if query and query not in ("scheda",):
            def _focus(q=query):
                title = focus_window(q)
                if title:
                    return
                subprocess.Popen(["cmd", "/c", "start", "", q], shell=False)
            return f"🪟 Passo a: {query}", _focus
    me = re.match(r"^(?:scheda|cambia scheda|vai alla scheda)\s+(.+)$", t)
    if me:
        query = me.group(1).strip()
        if query:
            def _tab(q=query):
                metodo, title = switch_tab(q)
                if title is None:
                    raise RuntimeError(f'nessuna scheda con "{q}"')
            return f"📑 Cerco scheda: {query}", _tab
    yt_words = has("youtube", "video", "trailer", "cerca")
    me = re.match(r"^(?:cerca|metti|apri)\s+(.+?)(?:\s+su\s+youtube)?$", t)
    if me and (yt_words or has("metti")):
        query = me.group(1).strip()
        if query and query not in ("youtube", "il video", "un video"):
            def open_yt(q=query):
                webbrowser.open("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(q))
            return f"🔎 YouTube: {query}", open_yt
    me = re.match(r"^apri\s+(.+)$", t)
    if me:
        target = me.group(1).strip()
        if target:
            def _open(tg=target):
                subprocess.Popen(["cmd", "/c", "start", "", tg], shell=False)
            return f"🚀 Apro: {target}", _open
    aliases = load_aliases()
    key = _norm(t)
    if key in aliases:
        target = aliases[key]
        def _alias(tg=target):
            open_target(tg)
        return f"✨ Alias «{key}»: apro {target}", _alias
    return None, None

# mappa callback -> azione
ACTIONS = {
    "play": ("⏯ Play/Pausa", lambda: press("space")),
    "fullscreen": ("⛶ Fullscreen (F)", lambda: press("f")),
    "mute_yt": ("M (muta video YT)", lambda: press("m")),
    "back10": ("⏪ -10s (←)", lambda: press("left")),
    "fwd10": ("10s+ (→) ⏩", lambda: press("right")),
    "vol_up": ("🔊 Vol +", lambda: press("volumeup")),
    "vol_down": ("🔉 Vol −", lambda: press("volumedown")),
    "vol_mute": ("🔇 Muta PC", lambda: press("volumemute")),
    "up": ("⬆️", lambda: press("up")),
    "down": ("⬇️", lambda: press("down")),
    "next": ("⏭ Next (N / media)", lambda: press("medianext")),
    "prev": ("⏮ Prev", lambda: press("mediaprev")),
    "esc": ("Esc", lambda: press("esc")),
    "close": ("✖ Chiudi tab (Ctrl+W)", lambda: press("ctrl", "w")),
}

def remote_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏯ Play/Pausa", callback_data="play"),
         InlineKeyboardButton("⛶ Fullscreen", callback_data="fullscreen")],
        [InlineKeyboardButton("⏪ -10s", callback_data="back10"),
         InlineKeyboardButton("⬆️", callback_data="up"),
         InlineKeyboardButton("⏩ +10s", callback_data="fwd10")],
        [InlineKeyboardButton("⬅️", callback_data="left5"),
         InlineKeyboardButton("⬇️", callback_data="down"),
         InlineKeyboardButton("➡️", callback_data="right5")],
        [InlineKeyboardButton("🔉 Vol −", callback_data="vol_down"),
         InlineKeyboardButton("🔇 Muta", callback_data="vol_mute"),
         InlineKeyboardButton("🔊 Vol +", callback_data="vol_up")],
        [InlineKeyboardButton("⏮ Prev", callback_data="prev"),
         InlineKeyboardButton("Esc", callback_data="esc"),
         InlineKeyboardButton("⏭ Next", callback_data="next")],
        [InlineKeyboardButton("M (muta video)", callback_data="mute_yt"),
         InlineKeyboardButton("✖ Chiudi tab", callback_data="close")],
        [InlineKeyboardButton("📸 Foto schermo", callback_data="shot"),
         InlineKeyboardButton("🎬 Telecomando", callback_data="show_remote")],
        [InlineKeyboardButton("😴 Sospendi", callback_data="power_sleep"),
         InlineKeyboardButton("💤 Spegni 5s", callback_data="power_off5"),
         InlineKeyboardButton("✅ Annulla", callback_data="power_abort")],
    ])

EXTRA_LEFT_RIGHT = {
    "left5": lambda: press("left"),
    "right5": lambda: press("right"),
}

def is_allowed(update: Update) -> bool:
    if not ALLOWED_IDS:
        return True  # modalità setup: permetti /start per mostrare il chat ID
    uid = str(update.effective_user.id)
    return uid in ALLOWED_IDS

def authorized(update: Update) -> bool:
    """True solo se ALLOWED_IDS configurato e l'utente è dentro. Tutto ciò che
    tocca il PC (tasti, comandi, file, vocali) deve passare da qui."""
    return bool(ALLOWED_IDS) and str(update.effective_user.id) in ALLOWED_IDS

# --- conferme Sì/No per azioni distruttive ---

import time as _time

PENDING: dict = {}  # user_id -> {"kind":..., "payload":..., "ts":...}

def confirm_kb(kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Sì, esegui", callback_data=f"cf:{kind}"),
        InlineKeyboardButton("❌ Annulla", callback_data="cx"),
    ]])

def ask_confirm(user_id: int, kind: str, payload: str = ""):
    PENDING[str(user_id)] = {"kind": kind, "payload": payload, "ts": _time.time()}

def pop_confirm(user_id: int, kind: str):
    p = PENDING.pop(str(user_id), None)
    if not p or p.get("kind") != kind:
        return None
    if _time.time() - p.get("ts", 0) > 300:
        return None  # scaduta dopo 5 min
    return p.get("payload", "")

DANGEROUS_CMD = re.compile(
    r"(?i)\b(del|erase|format|deltree|rd\s+/s|rmdir\s+/s|shutdown|taskkill|"
    r"reg\s+delete|remove-item|vssadmin|bcdedit|diskpart|cipher\s+/w|takeown|icacls)\b"
)

def single_instance_lock():
    """Evita doppie istanze (watchdog + avvio manuale). Ritorna False se un'altra istanza gira."""
    import msvcrt
    global _lock_fh
    try:
        _lock_fh = open(os.path.join(_base, "bot.lock"), "w")
        msvcrt.locking(_lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
        _lock_fh.write(str(os.getpid()))
        _lock_fh.flush()
        return True
    except (OSError, IOError):
        return False

async def deny(update: Update):
    uid = update.effective_user.id
    if not ALLOWED_IDS:
        await update.message.reply_text(
            "⛔ Bot non ancora blindato: imposta `ALLOWED_IDS` nel `.env` "
            "col tuo chat ID (mandami /start per vederlo) e riavvia. "
            "Finché non lo fai, i comandi PC restano bloccati.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"⛔ Non autorizzato.\nChat ID diverso dal solito: `{uid}`",
            parse_mode="Markdown",
        )

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if ALLOWED_IDS and not is_allowed(update):
        await update.message.reply_text(f"⛔ Non autorizzato.\nIl tuo chat ID è: `{uid}`", parse_mode="Markdown")
        return
    if not ALLOWED_IDS:
        await update.message.reply_text(
            f"👋 Ciao! Questo PC ora è il tuo telecomando.\n\n"
            f"Il tuo chat ID è: `{uid}`\n"
            f"Mettilo nel file `.env` come `ALLOWED_IDS={uid}` e riavvia il bot, "
            f"così solo tu puoi usarlo.\n\n"
            f"Intanto puoi già provare i pulsanti qui sotto.",
            parse_mode="Markdown",
            reply_markup=remote_keyboard(),
        )
        return
    await update.message.reply_text(
        "🎬 Telecomando pronto.\n\n"
        "🎤 VOCALI: manda un vocale («pausa», «tutto schermo», «alza il volume»,\n"
        "«manda avanti», «cerca trailer dune su youtube», «spegni»...)\n\n"
        "🌐 LINK / YOUTUBE — hai 3 modi:\n"
        "1. incolla un link (es. https://youtube.com/...) e lo apro sul PC\n"
        "2. /yt gattini divertenti → cerca su YouTube e apre i risultati\n"
        "3. /apri https://... → apre qualsiasi URL\n\n"
        "🔊 VOLUME: pulsanti 🔊/🔉/🔇, /vol 0-100 (PC) o /volyt 50 (player YouTube)\n"
        "⌨️ /scrivi <testo> scrive nella finestra attiva (per cercare serie)\n\n"
        "💻 CONTROLLO TOTALE (anche da fuori casa, guarda con AnyDesk):\n"
        "• /foto o scrivi `foto` → screenshot in chat\n"
        "• /cmd <comando> → esegue shell sul PC (es. /cmd dir)\n"
        "• /ls [cartella] → elenca file • /get <nome> → cerca e invia file (anche grandi, a pezzi)\n"
        "• /openapp <nome> o scrivi `apri chrome` → apre programmi\n"
        "• /finestra <nome> o `vai su discord` → porta l'app in primo piano (o la avvia)\n"
        "• /scheda <testo> o `scheda youtube` → passa alla scheda browser (anche stessa finestra)\n"
        "• /finestre → elenca finestre aperte\n"
        "• /chiudi <nome> o `chiudi discord` → chiude la finestra (/chiudi solo = chiudi tab)\n"
        "• /newscheda [url] → nuova scheda • /riapri → riapre ultima scheda chiusa\n"
        "• /riavvia [min] → riavvia (con conferma) • /schermo off|on → solo monitor\n"
        "• ✨ alias: scrivi `musica`/`film`, gestisci con /alias /aliasadd /aliasdel\n"
        "• /opencode <idea> → salva una richiesta di miglioramento per la prossima sessione\n"
        "• vocali extra: «fai uno screenshot» «apri notepad» «scrivi ciao»\n\n"
        "💤 SPEGNIMENTO (chiedono conferma Sì/No):\n"
        "• /spegni → spegne tra 5 secondi • /annulla → annulla\n"
        "• /sospendi → sospensione (da sospeso NON rispondo: riaccendi col tasto o Wake-on-LAN)",
        reply_markup=remote_keyboard(),
    )

async def buttons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not authorized(update):
        await q.edit_message_text(
            f"⛔ Non autorizzato. Chat ID diverso dal solito: `{update.effective_user.id}`",
            parse_mode="Markdown",
        )
        return
    action = q.data
    try:
        if action == "show_remote":
            await q.message.reply_text("🎬 Telecomando:", reply_markup=remote_keyboard())
            return
        if action == "shot":
            import tempfile
            import pyautogui
            pyautogui.FAILSAFE = False
            fd, path = tempfile.mkstemp(prefix="schermo_", suffix=".png")
            os.close(fd)
            try:
                pyautogui.screenshot(path)
                with open(path, "rb") as f:
                    await q.message.reply_photo(photo=f, caption="🖥️ Schermo del PC")
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            return
        if action == "cx":
            PENDING.pop(str(update.effective_user.id), None)
            await q.edit_message_text("❌ Annullato.")
            return
        if action.startswith("cf:"):
            kind = action.split(":", 1)[1]
            payload = pop_confirm(update.effective_user.id, kind)
            if payload is None:
                await q.edit_message_text("⌛ Richiesta scaduta o non valida, riprova.")
                return
            try:
                if kind == "spegni":
                    subprocess.Popen(["shutdown", "/s", "/t", "5"])
                    await q.edit_message_text("💤 Spegnimento tra 5 secondi. /annulla per fermarlo.")
                elif kind == "riavvia":
                    subprocess.Popen(["shutdown", "/r", "/t", str(int(payload) * 60)])
                    await q.edit_message_text(f"🔄 Riavvio tra {payload} min. /annulla per fermarlo.")
                elif kind == "sospendi":
                    await q.edit_message_text("😴 Sospendo ora. Da sospeso non rispondo: tasto fisico o Wake-on-LAN per riaccendere.")
                    subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
                elif kind == "cmd":
                    import asyncio as _aio
                    proc = await _aio.to_thread(
                        subprocess.run, payload,
                        shell=True, capture_output=True, text=True, errors="replace", timeout=25,
                    )
                    out = ((proc.stdout or "") + (("\n[STDERR]\n" + proc.stderr) if proc.stderr else "")).strip() or "(nessun output)"
                    if len(out) > 3500:
                        out = out[:3500] + "\n... (troncato)"
                    await q.edit_message_text(f"💻 `$ {payload}`\n```\n{out}\n```", parse_mode="Markdown")
            except Exception as e:
                await q.edit_message_text(f"Errore: {e}")
            return
        if action == "power_sleep":
            ask_confirm(update.effective_user.id, "sospendi", "")
            await q.message.reply_text("😴 Sospendere il PC? Da sospeso non rispondo più.", reply_markup=confirm_kb("sospendi"))
            return
        if action == "power_off5":
            ask_confirm(update.effective_user.id, "spegni", "0.0833")
            await q.message.reply_text("💤 Spegnere tra 5 secondi?", reply_markup=confirm_kb("spegni"))
            return
        if action == "power_abort":
            subprocess.Popen(["shutdown", "/a"])
            await q.message.reply_text("✅ Spegnimento annullato.")
            return
        if action in ACTIONS:
            ACTIONS[action][1]()
            label = ACTIONS[action][0]
        elif action in EXTRA_LEFT_RIGHT:
            EXTRA_LEFT_RIGHT[action]()
            label = action
        else:
            label = action
        # feedback minimo senza spam: nessun messaggio nuovo
        log.info("Eseguito %s per user %s", action, update.effective_user.id)
    except Exception as e:
        log.exception("Errore azione %s", action)
        await q.answer(f"Errore: {e}", show_alert=True)

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Testo libero: link -> apre; comandi naturali (foto, apri X, scrivi Y)."""
    if not authorized(update):
        await deny(update)
        return
    text = (update.message.text or "").strip()
    m = re.search(r"https?://\S+", text)
    if m:
        url = m.group(0).rstrip(").,]")
        try:
            webbrowser.open(url)
            await update.message.reply_text(f"🌐 Aperto sul PC:\n{url}", reply_markup=remote_keyboard())
        except Exception as e:
            await update.message.reply_text(f"Errore apertura link: {e}")
        return
    low = text.lower().strip()
    if low in ("screenshot", "foto", "foto schermo", "schermo", "mandami foto", "fammi vedere"):
        await send_screenshot(update)
        return
    me = re.match(r"^apri\s+(.+)$", low)
    if me:
        await open_app(update, me.group(1).strip())
        return
    me = re.match(r"^(?:vai su|passa a|portami (?:su|a)|finestra)\s+(.+)$", low)
    if me:
        import asyncio
        query = me.group(1).strip()
        title = await asyncio.to_thread(focus_window, query)
        if title:
            await update.message.reply_text(f"🪟 Ora in primo piano:\n{title}")
        else:
            await update.message.reply_text(f"🪟 `{query}` non trovata, la avvio...", parse_mode="Markdown")
            await open_app(update, query)
        return
    me = re.match(r"^(?:scheda|cambia scheda|vai alla scheda)\s+(.+)$", low)
    if me:
        import asyncio
        query = me.group(1).strip()
        status = await update.message.reply_text(f"📑 Cerco scheda `{query}`...", parse_mode="Markdown")
        metodo, title = await asyncio.to_thread(switch_tab, query)
        if title:
            dove = "altra finestra" if metodo == "finestra" else "stessa finestra (Ctrl+Tab)"
            await status.edit_text(f"📑 Trovata ({dove}):\n{title}")
        else:
            await status.edit_text(f"📑 Nessuna scheda con `{query}`.", parse_mode="Markdown")
        return
    me = re.match(r"^(?:scrivi|digita|detta)\s+(.+)$", text, re.IGNORECASE)
    if me:
        try:
            type_text(me.group(1))
            await update.message.reply_text("⌨️ Scritto.")
        except Exception as e:
            await update.message.reply_text(f"Errore: {e}")
        return
    me = re.match(r"^chiudi\s+(.+)$", low)
    if me:
        import asyncio
        query = me.group(1).strip()
        if query in ("tab", "scheda", "la scheda"):
            press("ctrl", "w")
            await update.message.reply_text("✖ Tab chiusa.")
        else:
            title = await asyncio.to_thread(close_window, query)
            await update.message.reply_text(f"✖ Chiusa:\n{title}" if title else f"Nessuna finestra con `{query}`.", parse_mode="Markdown" if not title else None)
        return
    if low in ("chiudi"):
        press("ctrl", "w")
        await update.message.reply_text("✖ Tab chiusa.")
        return
    me = re.match(r"^(?:nuova scheda|scheda nuova)\s*(.*)$", low)
    if me:
        import asyncio
        dest = me.group(1).strip()

        def _do(d=dest):
            import time
            press("ctrl", "t")
            time.sleep(0.6)
            if d:
                type_text(d)
                time.sleep(0.3)
                press("enter")
        await asyncio.to_thread(_do)
        await update.message.reply_text(f"📑 Nuova scheda{f' → {dest}' if dest else ''}.")
        return
    if low.startswith("riapri"):
        press("ctrl", "shift", "t")
        await update.message.reply_text("📑 Scheda riaperta.")
        return
    if low in ("schermo off", "spegni lo schermo", "spegni il monitor"):
        monitor_off()
        await update.message.reply_text("🖥️ Monitor spento. /schermo on o `schermo on` per riaccenderlo.")
        return
    if low in ("schermo on", "accendi lo schermo", "riaccendi il monitor"):
        monitor_on()
        await update.message.reply_text("🖥️ Monitor riacceso.")
        return
    me = re.match(r"^(?:volume|vol)(?:\s+(?:di|del))?\s+youtube\s+(.+)$", low)
    if me:
        import asyncio
        pct = parse_yt_volume(me.group(1))
        if pct is None:
            await update.message.reply_text("Uso: volume youtube 50")
            return
        status = await update.message.reply_text(f"🎚️ Imposto volume YouTube → {pct}%...")
        await asyncio.to_thread(set_youtube_volume, pct)
        await status.edit_text(f"🎚️ Volume YouTube → {pct}%")
        return
    aliases = load_aliases()
    if _norm(text) in aliases:
        target = aliases[_norm(text)]
        try:
            open_target(target)
            await update.message.reply_text(f"✨ Alias: apro {target}")
        except Exception as e:
            await update.message.reply_text(f"Errore: {e}")
        return
    # niente di riconosciuto: rimanda al telecomando
    await update.message.reply_text(
        "Non ho capito. Prova: un link, `foto`, `apri chrome`, `scrivi ciao`, /yt ..., /cmd ..., /foto",
        parse_mode="Markdown",
        reply_markup=remote_keyboard(),
    )

async def send_screenshot(update: Update):
    import tempfile
    import pyautogui
    pyautogui.FAILSAFE = False
    fd, path = tempfile.mkstemp(prefix="schermo_", suffix=".png")
    os.close(fd)
    try:
        pyautogui.screenshot(path)
        with open(path, "rb") as f:
            await update.message.reply_photo(photo=f, caption="🖥️ Schermo del PC")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

async def open_app(update: Update, target: str):
    """Apre un programma/file/URL via shell Windows."""
    try:
        subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)
        await update.message.reply_text(f"🚀 Apro: {target}")
    except Exception as e:
        await update.message.reply_text(f"Errore apertura {target}: {e}")

def _all_windows():
    import pygetwindow as gw
    out = []
    try:
        for w in gw.getAllWindows():
            try:
                t = (w.title or "").strip()
            except Exception:
                continue
            if t:
                out.append((w, t))
    except Exception:
        pass
    return out

def _match_score(title: str, query: str) -> int:
    """Punteggio match: tutte le parole della query nel titolo = ok, più punti se compaiono vicine/inizio."""
    tl = title.lower()
    words = [w for w in query.lower().split() if w]
    if not words or not all(w in tl for w in words):
        return -1
    score = 100 if tl.startswith(words[0]) else 0
    return score + sum(tl.count(w) for w in words)

def focus_window(query: str):
    """Porta in primo piano la finestra col titolo più simile. Ritorna il titolo o None."""
    import time
    best, best_score = None, -1
    for w, t in _all_windows():
        s = _match_score(t, query)
        if s > best_score:
            best, best_score = w, s
    if best is None:
        return None
    title = best.title
    try:
        if best.isMinimized:
            best.restore()
        best.activate()
    except Exception:
        try:
            best.minimize()
            time.sleep(0.2)
            best.restore()
            best.activate()
        except Exception:
            pass
    time.sleep(0.4)
    return title

def switch_tab(query: str, max_tabs: int = 15):
    """Cerca una scheda browser: prima tra le finestre, poi ciclando Ctrl+Tab nella finestra attiva.
    Ritorna (metodo, titolo) o (None, None)."""
    import time
    import pyautogui
    import pygetwindow as gw
    pyautogui.FAILSAFE = False
    title = focus_window(query)
    if title is not None:
        return "finestra", title
    for _ in range(max_tabs):
        pyautogui.hotkey("ctrl", "tab")
        time.sleep(0.45)
        try:
            w = gw.getActiveWindow()
            cur = (w.title or "") if w else ""
        except Exception:
            cur = ""
        if cur and _match_score(cur, query) >= 0:
            return "scheda", cur
    return None, None

def close_window(query: str):
    """Chiude la finestra col titolo più simile. Ritorna il titolo o None."""
    import time
    best, best_score = None, -1
    for w, t in _all_windows():
        s = _match_score(t, query)
        if s > best_score:
            best, best_score = w, s
    if best is None:
        return None
    title = best.title
    try:
        best.close()
    except Exception:
        return None
    time.sleep(0.4)
    return title

# --- alias personali ("musica" -> spotify, ...) ---

def _alias_path() -> str:
    return os.path.join(_base, "aliases.json")

def load_aliases() -> dict:
    import json
    defaults = {"musica": "spotify", "film": "netflix"}
    try:
        with open(_alias_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k).lower(): str(v) for k, v in data.items()}
    except (OSError, ValueError):
        pass
    save_aliases(defaults)
    return defaults

def save_aliases(d: dict):
    import json
    with open(_alias_path(), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", s.lower())).strip()

def open_target(target: str):
    """Apre URL o programma, usato da /apri e alias."""
    if re.search(r"https?://\S+", target):
        webbrowser.open(target)
    else:
        subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)

async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Vocale Telegram -> trascrizione offline -> comando PC."""
    if not authorized(update):
        await deny(update)
        return
    import asyncio
    import tempfile
    voice = update.message.voice
    if voice is None:
        return
    status = await update.message.reply_text("🎤 Ascolto...")
    tmp = tempfile.mkdtemp(prefix="vocale_")
    ogg_path = os.path.join(tmp, "vocale.ogg")
    try:
        tg_file = await voice.get_file()
        await tg_file.download_to_drive(ogg_path)
        testo = await asyncio.to_thread(transcribe_voice, ogg_path)
    except Exception as e:
        log.exception("trascrizione vocale")
        await status.edit_text(f"🎤 Non riesco a trascrivere ({e}).")
        return
    finally:
        try:
            os.remove(ogg_path)
            os.rmdir(tmp)
        except OSError:
            pass
    if not testo:
        await status.edit_text("🎤 Non ho capito l'audio, riprova.")
        return
    label, action = match_voice_command(testo)
    if action is None:
        await status.edit_text(
            f'🎤 Ho capito: "{testo}"\n'
            "ma non so a quale comando corrisponde. Prova con:\n"
            "«pausa» «tutto schermo» «alza il volume» «manda avanti»\n"
            "«cerca trailer dune su youtube» «fai uno screenshot» «apri chrome»",
            reply_markup=remote_keyboard(),
        )
        return
    if label == "__SCREENSHOT__":
        try:
            await status.delete()
        except Exception:
            pass
        await send_screenshot(update)
        return
    if label.startswith("__CF:"):
        _, kind, payload = label.split(":", 2)
        ask_confirm(update.effective_user.id, kind, payload)
        what = {"spegni": "💤 Spegnere tra 5 secondi",
                "sospendi": "😴 Sospendere il PC",
                "riavvia": f"🔄 Riavviare tra {payload} min",
                "cmd": f"💻 Eseguire `{payload}`"}.get(kind, kind)
        await status.edit_text(
            f'🎤 "{testo}"\nConfermi {what}?', parse_mode="Markdown",
            reply_markup=confirm_kb(kind),
        )
        return
    try:
        action()
        await status.edit_text(f'🎤 "{testo}"\n✅ {label}', reply_markup=remote_keyboard())
    except Exception as e:
        await status.edit_text(f'🎤 "{testo}"\n⚠️ Errore: {e}')

async def vol(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await deny(update)
        return
    if not ctx.args:
        await update.message.reply_text("Uso: /vol 0-100  (es. /vol 30)")
        return
    try:
        pct = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("Dammi un numero 0-100.")
        return
    try:
        set_volume_percent(pct)
        await update.message.reply_text(f"🔊 Volume impostato a {pct}%")
    except Exception as e:
        log.exception("set volume")
        await update.message.reply_text(
            f"Non riesco a impostare il volume assoluto ({e}).\n"
            "Usa i pulsanti 🔊/🔉 oppure installa pycaw: pip install pycaw"
        )

async def scrivi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await deny(update)
        return
    testo = update.message.text.partition(" ")[2]
    if not testo.strip():
        await update.message.reply_text("Uso: /scrivi breaking bad trailer")
        return
    try:
        type_text(testo)
        await update.message.reply_text("⌨️ Scritto.")
    except Exception as e:
        await update.message.reply_text(f"Errore: {e}")

async def blocca(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await deny(update)
        return
    subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
    await update.message.reply_text("🔒 PC bloccato.")

async def sospendi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/sospendi -> chiede conferma, poi sospensione."""
    if not authorized(update):
        await deny(update)
        return
    ask_confirm(update.effective_user.id, "sospendi", "")
    await update.message.reply_text(
        "😴 Sospendere il PC? Da sospeso NON rispondo più: "
        "riaccendi col tasto fisico o Wake-on-LAN.",
        reply_markup=confirm_kb("sospendi"),
    )

async def yt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Cerca su YouTube e apre i risultati sul PC. Es: /yt trailer dune 2"""
    if not authorized(update):
        await deny(update)
        return
    query = " ".join(ctx.args) if ctx.args else update.message.text.partition(" ")[2]
    if not query.strip():
        await update.message.reply_text("Uso: /yt gattini divertenti")
        return
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
    webbrowser.open(url)
    await update.message.reply_text(f"🔎 Cerco su YouTube (PC):\n{query}\n{url}", reply_markup=remote_keyboard())

async def apri(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Apre URL o programmi. Es: /apri https://... | /apri chrome | /apri notepad"""
    if not authorized(update):
        await deny(update)
        return
    arg = " ".join(ctx.args).strip() if ctx.args else update.message.text.partition(" ")[2].strip()
    if not arg:
        await update.message.reply_text("Uso: /apri https://...  oppure  /apri chrome")
        return
    m = re.search(r"https?://\S+", arg)
    if m:
        url = m.group(0).rstrip(").,]")
        webbrowser.open(url)
        await update.message.reply_text(f"🌐 Aperto sul PC:\n{url}", reply_markup=remote_keyboard())
    else:
        await open_app(update, arg)

async def spegni(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await deny(update)
        return
    ask_confirm(update.effective_user.id, "spegni", "0.0833")
    await update.message.reply_text(
        "💤 Spegnere tra 5 secondi?", reply_markup=confirm_kb("spegni"),
    )

async def riavvia(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/riavvia [min] -> chiede conferma, poi riavvia."""
    if not authorized(update):
        await deny(update)
        return
    minuti = 1
    if ctx.args:
        try:
            minuti = max(0, int(ctx.args[0]))
        except ValueError:
            pass
    ask_confirm(update.effective_user.id, "riavvia", str(minuti))
    await update.message.reply_text(
        f"🔄 Riavviare tra {minuti} min? (Il bot riparte da solo al login)",
        reply_markup=confirm_kb("riavvia"),
    )

async def volyt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/volyt 50 -> volume del player YouTube/Netflix (richiede video in primo piano)."""
    import asyncio
    if not authorized(update):
        await deny(update)
        return
    arg = (" ".join(ctx.args) if ctx.args else update.message.text.partition(" ")[2])
    pct = parse_yt_volume(arg)
    if pct is None:
        await update.message.reply_text("Uso: /volyt 50  (0-100, serve il video in primo piano)")
        return
    status = await update.message.reply_text(f"🎚️ Imposto volume YouTube → {pct}%...")
    await asyncio.to_thread(set_youtube_volume, pct)
    await status.edit_text(f"🎚️ Volume YouTube → {pct}% (video in primo piano)")

async def schermo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/schermo off|on -> spegne/riaccende solo il monitor."""
    if not authorized(update):
        await deny(update)
        return
    arg = (" ".join(ctx.args).strip().lower() if ctx.args else "")
    if arg in ("off", "spento", "spegni"):
        monitor_off()
        await update.message.reply_text("🖥️ Monitor spento (PC e bot restano attivi). /schermo on per riaccenderlo.")
    elif arg in ("on", "acceso", "accendi"):
        monitor_on()
        await update.message.reply_text("🖥️ Monitor riacceso.")
    else:
        await update.message.reply_text("Uso: /schermo off  oppure  /schermo on")

async def newscheda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/newscheda [url o ricerca] -> nuova scheda browser (sulla finestra attiva)."""
    import asyncio as _aio
    if not authorized(update):
        await deny(update)
        return
    arg = (" ".join(ctx.args).strip() if ctx.args else update.message.text.partition(" ")[2].strip())
    def _do(dest=arg):
        import time
        press("ctrl", "t")
        time.sleep(0.6)
        if dest:
            type_text(dest)
            time.sleep(0.3)
            press("enter")
    await _aio.to_thread(_do)
    await update.message.reply_text(f"📑 Nuova scheda{f' → {arg}' if arg else ''} (sulla finestra attiva).")

async def riapri(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/riapri -> riapre l'ultima scheda chiusa (Ctrl+Maiusc+T)."""
    import asyncio as _aio
    if not authorized(update):
        await deny(update)
        return
    await _aio.to_thread(lambda: press("ctrl", "shift", "t"))
    await update.message.reply_text("📑 Scheda riaperta (se il browser ne ha una in memoria).")

async def chiudi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/chiudi <nome> -> chiude la finestra. /chiudi senza args = chiudi tab browser."""
    import asyncio
    if not authorized(update):
        await deny(update)
        return
    query = (" ".join(ctx.args).strip() if ctx.args else "")
    if not query:
        press("ctrl", "w")
        await update.message.reply_text("✖ Tab chiusa.")
        return
    title = await asyncio.to_thread(close_window, query)
    if title:
        await update.message.reply_text(f"✖ Chiusa:\n{title}")
    else:
        await update.message.reply_text(f"Nessuna finestra con `{query}`.", parse_mode="Markdown")

async def alias_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/alias -> elenca gli alias personali."""
    if not authorized(update):
        await deny(update)
        return
    d = load_aliases()
    lines = ["✨ Alias personali (scrivi o detta il nome):"] + [f"• `{k}` → {v}" for k, v in sorted(d.items())]
    lines.append("\n/aliasadd nome cosa_aprire • /aliasdel nome")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def aliasadd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/aliasadd musica spotify"""
    if not authorized(update):
        await deny(update)
        return
    parts = (update.message.text.partition(" ")[2]).split(None, 1)
    if len(parts) != 2:
        await update.message.reply_text("Uso: /aliasadd musica spotify")
        return
    d = load_aliases()
    d[parts[0].lower()] = parts[1]
    save_aliases(d)
    await update.message.reply_text(f"✨ Alias `{parts[0].lower()}` → {parts[1]} salvato.", parse_mode="Markdown")

async def aliasdel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/aliasdel musica"""
    if not authorized(update):
        await deny(update)
        return
    name = (" ".join(ctx.args).strip().lower() if ctx.args else "")
    d = load_aliases()
    if name in d:
        del d[name]
        save_aliases(d)
        await update.message.reply_text(f"✨ Alias `{name}` eliminato.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"Nessun alias `{name}`.", parse_mode="Markdown")

async def annulla(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await deny(update)
        return
    subprocess.Popen(["shutdown", "/a"])
    await update.message.reply_text("✅ Spegnimento annullato.")

async def foto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Manda uno screenshot del PC. Es: /foto"""
    if not authorized(update):
        await deny(update)
        return
    await send_screenshot(update)

async def cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Esegue un comando shell sul PC e restituisce l'output. Es: /cmd dir C:\\"""
    import asyncio
    if not authorized(update):
        await deny(update)
        return
    command = update.message.text.partition(" ")[2].strip()
    if not command:
        await update.message.reply_text("Uso: /cmd dir C:\\   (esegue sul PC, max 25s)")
        return
    log.info("CMD da %s: %s", update.effective_user.id, command)
    if DANGEROUS_CMD.search(command):
        ask_confirm(update.effective_user.id, "cmd", command)
        await update.message.reply_text(
            f"⚠️ Comando potenzialmente distruttivo:\n`{command}`\nEseguire davvero?",
            parse_mode="Markdown",
            reply_markup=confirm_kb("cmd"),
        )
        return
    try:
        proc = await asyncio.to_thread(
            subprocess.run, command,
            shell=True, capture_output=True, text=True, errors="replace", timeout=25,
        )
        out = (proc.stdout or "") + (("\n[STDERR]\n" + proc.stderr) if proc.stderr else "")
        out = out.strip() or "(nessun output)"
        if len(out) > 3500:
            out = out[:3500] + "\n... (troncato)"
        await update.message.reply_text(f"💻 `$ {command}`\n```\n{out}\n```", parse_mode="Markdown")
    except subprocess.TimeoutExpired:
        await update.message.reply_text("⏱ Comando oltre i 25s, interrotto.")
    except Exception as e:
        await update.message.reply_text(f"Errore: {e}")

async def ls(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/ls [percorso] -> elenca file/cartelle. Default: cartella utente."""
    if not authorized(update):
        await deny(update)
        return
    path = (" ".join(ctx.args).strip() if ctx.args else "") or os.path.expanduser("~")
    try:
        entries = sorted(os.listdir(path))
    except Exception as e:
        await update.message.reply_text(f"Errore: {e}")
        return
    lines = [f"📁 {os.path.abspath(path)}"]
    for e in entries[:60]:
        full = os.path.join(path, e)
        lines.append(("📁 " if os.path.isdir(full) else "📄 ") + e)
    if len(entries) > 60:
        lines.append(f"... e altri {len(entries) - 60}")
    await update.message.reply_text("```\n" + "\n".join(lines) + "\n```", parse_mode="Markdown")

async def getfile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/get <nome o percorso> -> invia il file in chat.
    Basta il nome: lo cerca in Desktop/Documenti/Download/Home.
    File grandi: inviati a pezzi da ~45MB (Telegram non accetta oltre ~50MB)."""
    import asyncio
    import tempfile
    if not authorized(update):
        await deny(update)
        return
    arg = (" ".join(ctx.args).strip() if ctx.args else "")
    if not arg:
        await update.message.reply_text("Uso: /get nomefile.txt  oppure  /get C:\\percorso\\file.txt")
        return
    if os.path.isfile(arg):
        path = os.path.abspath(arg)
    else:
        status = await update.message.reply_text(f"🔎 Cerco `{arg}`...", parse_mode="Markdown")
        found = await asyncio.to_thread(find_file, arg)
        if not found:
            await status.edit_text(f"Nessun file di nome `{arg}` in Desktop/Documenti/Download/Home.", parse_mode="Markdown")
            return
        if len(found) > 1:
            elenco = "\n".join("• `" + p + "`" for p in found[:10])
            await status.edit_text(
                f"Trovati {len(found)}, specifica meglio con /get + percorso:\n{elenco}",
                parse_mode="Markdown",
            )
            return
        path = found[0]
        await status.delete()
    size = os.path.getsize(path)
    name = os.path.basename(path)
    CHUNK = 45 * 1024 * 1024
    try:
        if size <= CHUNK:
            await update.message.reply_text(f"📄 Invio `{name}` ({size // 1024} KB)...", parse_mode="Markdown")
            with open(path, "rb") as f:
                await update.message.reply_document(document=f, filename=name)
        else:
            n = (size + CHUNK - 1) // CHUNK
            await update.message.reply_text(
                f"📦 `{name}` è grande ({size // (1024*1024)} MB): lo invio in {n} parti.\n"
                f"Per ricomporlo sul PC: `copy /b \"{name}.part*\" \"{name}\"`",
                parse_mode="Markdown",
            )
            with open(path, "rb") as f:
                for i in range(n):
                    data = f.read(CHUNK)
                    bio = _BytesDoc(data, f"{name}.part{i+1:03d}")
                    await update.message.reply_document(document=bio, filename=bio.name)
    except Exception as e:
        await update.message.reply_text(f"Errore: {e}")


class _BytesDoc:
    """File-like da bytes con .name, per inviare pezzi senza file temporanei."""
    def __init__(self, data: bytes, name: str):
        import io
        self._io = io.BytesIO(data)
        self.name = name
    def read(self, *a):
        return self._io.read(*a)


def find_file(name: str, timeout_s: float = 20.0):
    """Cerca un file per nome (case-insensitive) nelle cartelle comuni."""
    import time
    home = os.path.expanduser("~")
    roots = [
        os.path.join(home, "Desktop"),
        os.path.join(home, "Documents"),
        os.path.join(home, "Downloads"),
        os.path.join(home, "Videos"),
        home,
    ]
    target = name.lower()
    deadline = time.time() + timeout_s
    hits = []
    seen = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if time.time() > deadline or len(hits) >= 10:
                return hits
            if dirpath.count(os.sep) - base_depth > 4:
                dirnames[:] = []
                continue
            # salta cartelle pesanti/inutili
            dirnames[:] = [d for d in dirnames if d.lower() not in (
                "node_modules", ".git", "__pycache__", "appdata",
                "application data", "$recycle.bin", "system volume information",
            )]
            for fn in filenames:
                if fn.lower() == target or (target in fn.lower() and len(target) >= 4):
                    p = os.path.abspath(os.path.join(dirpath, fn))
                    key = os.path.normcase(p)
                    if key not in seen:
                        seen.add(key)
                        hits.append(p)
                    if len(hits) >= 10:
                        return hits
    return hits


def salva_richiesta(testo: str, origine: str = "telegram") -> str:
    """Salva una richiesta per OpenCode in richieste.md, restituisce il percorso."""
    import time
    path = os.path.join(_base, "richieste.md")
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as f:
        if new:
            f.write("# Richieste a OpenCode (arrivate da Telegram)\n\n")
        f.write(f"## {time.strftime('%Y-%m-%d %H:%M')} ({origine})\n\n{testo.strip()}\n\n")
    return path


async def opencode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/opencode <richiesta> -> salva un promemoria per miglioramenti futuri."""
    if not authorized(update):
        await deny(update)
        return
    testo = update.message.text.partition(" ")[2].strip()
    if not testo:
        await update.message.reply_text("Uso: /opencode aggiungi pulsante per Netflix")
        return
    path = salva_richiesta(testo)
    await update.message.reply_text(
        "📝 Richiesta salvata su questo PC (`pc-remote-telegram/richieste.md`).\n\n"
        "Limite onesto: da remoto non posso mettermi a programmare da solo — "
        "non ho un agente sempre attivo sul PC. Quello che salvi qui lo trovo "
        "alla prossima sessione insieme e lo implemento. Per cose urgenti usa "
        "/cmd (comandi diretti) o AnyDesk.",
        reply_markup=remote_keyboard(),
    )

async def openapp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/openapp <programma> -> es: /openapp notepad | chrome | calc."""
    if not authorized(update):
        await deny(update)
        return
    target = (" ".join(ctx.args).strip() if ctx.args else update.message.text.partition(" ")[2].strip())
    if not target:
        await update.message.reply_text("Uso: /openapp notepad")
        return
    await open_app(update, target)

async def finestra(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/finestra <nome> -> porta in primo piano l'app (o la avvia se chiusa). Es: /finestra discord"""
    import asyncio
    if not authorized(update):
        await deny(update)
        return
    query = (" ".join(ctx.args).strip() if ctx.args else update.message.text.partition(" ")[2].strip())
    if not query:
        await update.message.reply_text("Uso: /finestra discord")
        return
    title = await asyncio.to_thread(focus_window, query)
    if title:
        await update.message.reply_text(f"🪟 Ora in primo piano:\n{title}")
    else:
        await update.message.reply_text(f"🪟 `{query}` non trovata tra le finestre aperte, la avvio...", parse_mode="Markdown")
        await open_app(update, query)

async def scheda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/scheda <testo> -> passa alla scheda browser che contiene il testo. Es: /scheda youtube"""
    import asyncio
    if not authorized(update):
        await deny(update)
        return
    query = (" ".join(ctx.args).strip() if ctx.args else update.message.text.partition(" ")[2].strip())
    if not query:
        await update.message.reply_text("Uso: /scheda youtube")
        return
    status = await update.message.reply_text(f"📑 Cerco scheda `{query}`...", parse_mode="Markdown")
    metodo, title = await asyncio.to_thread(switch_tab, query)
    if title:
        dove = "altra finestra" if metodo == "finestra" else "stessa finestra (Ctrl+Tab)"
        await status.edit_text(f"📑 Trovata ({dove}):\n{title}")
    else:
        await status.edit_text(f"📑 Nessuna scheda con `{query}` trovata.", parse_mode="Markdown")

async def finestre(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/finestre -> elenca le finestre aperte (per sapere cosa chiedere)."""
    import asyncio
    if not authorized(update):
        await deny(update)
        return
    wins = await asyncio.to_thread(_all_windows)
    titles = sorted({t for _, t in wins})[:40]
    if not titles:
        titles = ["(nessuna finestra rilevata)"]
    await update.message.reply_text("🪟 Finestre aperte:\n```\n" + "\n".join(titles) + "\n```", parse_mode="Markdown")

def main():
    if not TOKEN or TOKEN == "INCOLLA_QUI_IL_TOKEN":
        print("ERRORE: metti il token di BotFather nel file .env (vedi .env.example).")
        raise SystemExit(1)
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("telecomando", start))
    app.add_handler(CommandHandler("vol", vol))
    app.add_handler(CommandHandler("volyt", volyt))
    app.add_handler(CommandHandler("scrivi", scrivi))
    app.add_handler(CommandHandler("blocca", blocca))
    app.add_handler(CommandHandler("sospendi", sospendi))
    app.add_handler(CommandHandler("spegni", spegni))
    app.add_handler(CommandHandler("annulla", annulla))
    app.add_handler(CommandHandler("yt", yt))
    app.add_handler(CommandHandler("apri", apri))
    app.add_handler(CommandHandler("foto", foto))
    app.add_handler(CommandHandler("cmd", cmd))
    app.add_handler(CommandHandler("ls", ls))
    app.add_handler(CommandHandler("get", getfile))
    app.add_handler(CommandHandler("openapp", openapp))
    app.add_handler(CommandHandler("chiudi", chiudi))
    app.add_handler(CommandHandler("newscheda", newscheda))
    app.add_handler(CommandHandler("riapri", riapri))
    app.add_handler(CommandHandler("riavvia", riavvia))
    app.add_handler(CommandHandler("schermo", schermo))
    app.add_handler(CommandHandler("alias", alias_cmd))
    app.add_handler(CommandHandler("aliasadd", aliasadd))
    app.add_handler(CommandHandler("aliasdel", aliasdel))
    app.add_handler(CommandHandler("finestra", finestra))
    app.add_handler(CommandHandler("scheda", scheda))
    app.add_handler(CommandHandler("finestre", finestre))
    app.add_handler(CommandHandler("opencode", opencode))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    if not single_instance_lock():
        print("Un'altra istanza è già in esecuzione, esco.")
        raise SystemExit(0)
    print("Telecomando avviato. Vai su Telegram e manda /start al tuo bot.")
    app.run_polling()

if __name__ == "__main__":
    main()
