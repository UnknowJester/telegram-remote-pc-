# Telecomando PC via Telegram

Pensato per: sei a letto a guardare YouTube / serie e vuoi play/pausa, avanti/indietro, volume, fullscreen senza alzarti.

## 1. Crea il bot (2 min)
1. Su Telegram cerca **@BotFather** → `/newbot` → dagli un nome → copia il **token**.
2. Apri il file `.env` in questa cartella e incolla: `TELEGRAM_TOKEN=...`

## 2. Avvia
Doppio clic su `start.bat`. La prima volta installa le dipendenze.

## 3. Autorizza solo te (importante)
1. Su Telegram manda `/start` al tuo bot.
2. Il bot ti risponde con il tuo **chat ID**.
3. Mettilo nel `.env`: `ALLOWED_IDS=123456789` e riavvia `start.bat`.

## Cosa puoi fare da Telegram
- Pulsanti: ⏯ play/pausa (spazio), ⏪/⏩ ±10s (frecce), ⬆️⬇️ (volume/scorrimento YouTube), ⛶ fullscreen (F), M muta video, ⏭/⏮ next/prev, Esc, ✖ chiudi tab
- 🎤 **Vocali**: manda un vocale con «pausa», «tutto schermo», «alza/abbassa il volume», «muta», «manda avanti/indietro», «prossimo episodio», «cerca trailer dune su youtube», «blocca il computer», «spegni». Trascrizione offline sul PC, gratis.
- Manda un **link** e si apre sul PC (es. link YouTube dal telefono)
- `/vol 30` → volume PC al 30% • `/volyt 50` o `volume youtube 50` → volume player YouTube
- `/scrivi breaking bad` → scrive nella finestra attiva (per cercare)
- `/foto` → screenshot del PC in chat (oppure scrivi `foto`)
- `/cmd dir` → esegue comandi shell e mostra output (max 25s, i pericolosi chiedono conferma)
- `/ls [cartella]` → elenca file, `/get <nome>` → cerca e invia file (anche grandi, a pezzi)
- `/openapp chrome` o scrivi `apri notepad` → apre programmi
- `/finestra discord` o `vai su brave` → porta app in primo piano (o la avvia)
- `/scheda loonex` → passa alla scheda browser • `/finestre` → elenca finestre
- `/chiudi discord` → chiude la finestra (`/chiudi` solo = chiudi tab)
- `/riavvia 1` → riavvia (con conferma) • `/schermo off|on` → solo monitor
- ✨ alias: `musica`→Spotify, `film`→Netflix; `/alias` lista, `/aliasadd`, `/aliasdel`
- `/blocca` → blocca PC
- `/spegni 1` → spegne tra 1 minuto, `/annulla` → annulla
- `/sospendi` → sospensione (conferma Sì/No; da sospeso il bot non risponde)
- Log errori in `bot.log`, riavvio automatico via `watchdog.vbs` (Esecuzione automatica)

## Note
- Funziona solo mentre il PC è acceso e `start.bat` è in esecuzione.
- Il PC deve avere Telegram connesso a internet, non serve stessa rete Wi-Fi.
- Se vuoi avvio automatico: Win+R → `shell:startup` → crea collegamento a `start.bat`.
