#!/usr/bin/env python3
"""Prova della Sveglia senza GitHub: esegue il VERO script di sveglia.yml con orologio finto.

    python3 test/sveglia_sim.py [percorso/sveglia.yml]

`date`, `sleep` e `gh` sono sostituiti da funzioni bash: niente attese vere, nessun lancio vero.
Ogni lancio della catena parte dopo la latenza dello scenario; i cron (se ci sono) cancellano il turno in corso,
come fa `cancel-in-progress`. Vale solo con l'ora invernale (Canarie = UTC), quella delle uscite di novembre.
"""
import calendar, os, re, subprocess, sys, tempfile

YML = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), '..', '.github', 'workflows', 'sveglia.yml')
testo = open(YML, encoding='utf8').read()
corpo = re.search(r'- run: \|\n(.*?)\n\s+env:', testo, re.S).group(1)
corpo = '\n'.join(r[10:] for r in corpo.split('\n'))

STUB = r'''
ora() { local x; read -r x < "$OROLOGIO"; echo "$x"; }
date() {
  local now; read -r now < "$OROLOGIO"
  case "$1" in
    +%s) echo "$now" ;;
    +%-H) echo $(( now % 86400 / 3600 )) ;;
    -d) local g=${2%% *} hm=${2##* } base=$(( now / 86400 * 86400 ))
        [ "$g" = tomorrow ] && base=$(( base + 86400 ))
        echo $(( base + 10#${hm%%:*} * 3600 + 10#${hm##*:} * 60 )) ;;
    *) echo "T$now" ;;
  esac
}
sleep() {
  local now arrivo; read -r now < "$OROLOGIO"; arrivo=$(( now + $1 ))
  if [ -n "$PROSSIMO_CRON" ] && [ "$PROSSIMO_CRON" -le "$arrivo" ]; then
    echo "$PROSSIMO_CRON" > "$OROLOGIO"; exit 143   # il cron cancella questo turno
  fi
  echo "$arrivo" > "$OROLOGIO"
}
gh() {
  local now; read -r now < "$OROLOGIO"; echo $(( now + 1 )) > "$OROLOGIO"
  case "$*" in
    *sveglia.yml*) echo "sveglia $now" >> "$REGISTRO"
      if [ -n "$FALLISCI_SVEGLIA_DOPO" ] && [ "$now" -ge "$FALLISCI_SVEGLIA_DOPO" ] && [ ! -e "$REGISTRO.fallita" ]; then
        : > "$REGISTRO.fallita"; return 1; fi ;;
    *pubblica.yml*) echo "pubblica $now" >> "$REGISTRO" ;;
    *) echo "gh sconosciuto: $*" >&2; return 1 ;;
  esac
}
'''

def ts(a, me, g, h=0, m=0):
    return calendar.timegm((a, me, g, h, m, 0))

INIZIO = ts(2026, 11, 2, 0, 0)  # lunedì
GIORNI = 8
USCITE = {'mar 3/11 12:30': ts(2026,11,3,12,30), 'mar 3/11 18:00': ts(2026,11,3,18), 'mer 4/11 18:00': ts(2026,11,4,18),
          'gio 5/11 18:00': ts(2026,11,5,18), 'sab 7/11 11:30': ts(2026,11,7,11,30), 'dom 8/11 12:00': ts(2026,11,8,12),
          'lun 9/11 12:00': ts(2026,11,9,12), 'lun 9/11 12:30': ts(2026,11,9,12,30)}
FINESTRA = 3 * 3600

def simula(latenza, cron_ritardo=None, fallisci_dopo=None, inizio=INIZIO, giorni=GIORNI, max_turni=100000):
    fine_sim = inizio + giorni * 86400
    with tempfile.TemporaryDirectory() as d:
        oro, reg = os.path.join(d, 'o'), os.path.join(d, 'r')
        sc = os.path.join(d, 's.sh')
        open(sc, 'w').write(STUB + corpo + '\n')
        open(oro, 'w').write(f'{inizio}\n'); open(reg, 'w').close()
        durate, turni, cancellati, crons_usati = [], 0, 0, 0
        def prossimo_cron(t):
            if cron_ritardo is None:
                return None
            c = t // 10800 * 10800 + cron_ritardo
            while c <= t:
                c += 10800
            return c
        vivo = True; t = inizio
        while t < fine_sim and turni < max_turni:
            open(oro, 'w').write(f'{t}\n')
            env = dict(os.environ, OROLOGIO=oro, REGISTRO=reg, PROSSIMO_CRON=str(prossimo_cron(t) or ''),
                       FALLISCI_SVEGLIA_DOPO=str(fallisci_dopo or ''))
            r = subprocess.run(['bash', '-e', sc], env=env, capture_output=True, text=True)
            turni += 1
            fine = int(open(oro).read())
            durate.append(fine - t)
            if r.returncode == 143:            # cancellato dal cron: il turno nuovo parte subito
                cancellati += 1; t = fine; continue
            if r.returncode == 0:
                t = fine + latenza; continue
            c = prossimo_cron(fine)            # catena interrotta: resta solo il cron di riserva
            if c is None:
                vivo = False; break
            crons_usati += 1; t = c
        ev = [x.split() for x in open(reg).read().split('\n') if x]
    pub = sorted(int(v) for k, v in ev if k == 'pubblica')
    sv = sum(1 for k, _ in ev if k == 'sveglia')
    # ogni fascia: un lancio entro 15 minuti dall'orario previsto (più la latenza)
    buchi = []
    for g in range(giorni):
        base = inizio // 86400 * 86400 + g * 86400
        for h in (12, 13, 14, 18, 19, 20, 21):
            for m in (2, 17, 32, 47):
                s = base + h * 3600 + m * 60
                if inizio <= s < fine_sim and not any(s <= p <= s + 900 + latenza for p in pub):
                    buchi.append(s)
    ritardi = {}
    for nome, q in USCITE.items():
        if inizio <= q < fine_sim:
            dopo = [p for p in pub if p >= q]
            ritardi[nome] = (dopo[0] - q) if dopo else None
    return dict(turni=turni, cancellati=cancellati, rilanci_al_giorno=round(sv / giorni, 1), max_durata_min=max(durate) // 60,
                buchi=buchi, ritardi=ritardi, vivo=vivo, pubblica_lanci=len(pub))

def stampa(nome, r):
    peggiore = max((v if v is not None else 10**9) for v in r['ritardi'].values()) if r['ritardi'] else 0
    ok = not r['buchi'] and peggiore <= FINESTRA and r['max_durata_min'] <= 350
    print(f"{'OK ' if ok else 'KO '} {nome}: turni {r['turni']} (cancellati {r['cancellati']}), rilanci/giorno {r['rilanci_al_giorno']}, "
          f"turno più lungo {r['max_durata_min']} min, lanci Pubblica {r['pubblica_lanci']}, fasce scoperte {len(r['buchi'])}, "
          f"ritardo massimo uscita {peggiore // 60} min")
    if r['ritardi']:
        print('     lun 9/11 12:00 →', r['ritardi'].get('lun 9/11 12:00'), 's; sab 7/11 11:30 →', r['ritardi'].get('sab 7/11 11:30'), 's')
    return ok

if __name__ == '__main__':
    tutto = True
    for nome, kw in [('catena sola, latenza 10 s', dict(latenza=10)),
                     ('catena sola, latenza 60 s', dict(latenza=60)),
                     ('catena sola, latenza 5 min', dict(latenza=300)),
                     ('catena + cron puntuali', dict(latenza=30, cron_ritardo=0)),
                     ('catena + cron in ritardo di 40 min', dict(latenza=30, cron_ritardo=2400)),
                     ('rilancio fallito il 8/11 alle 22, si riprende col cron', dict(latenza=30, cron_ritardo=2400, fallisci_dopo=ts(2026,11,8,22)))]:
        tutto &= stampa(nome, simula(**kw))
    for ore in range(0, 24, 3):
        r = simula(latenza=20, inizio=INIZIO + ore * 3600 + 7 * 60, giorni=3)
        tutto &= stampa(f'partenza alle {ore:02d}:07', r)
    sys.exit(0 if tutto else 1)
