#!/usr/bin/env sh
# Guided installer for the Korodexios core (kdx) on Linux and macOS.
#
# Four clearly separated steps - check, install, verify, first project - with colour and
# plain explanations. It asks before installing uv or creating anything and never
# touches system settings.
#
#   ./install.sh                 interactive
#   ./install.sh --lang sk       Slovak messages
#   ./install.sh --yes           answer yes to every question
#   ./install.sh --dry-run       show every step, run nothing
#   ./install.sh --no-path-update  do not let uv edit your PATH / shell profile
#   ./install.sh --source SRC    install SRC instead of this folder / the released tag

set -eu

RELEASE_SOURCE="korodexios-core @ git+https://github.com/korodexios/korodexios-core.git@v0.5.4"
SOURCE=""
LANGUAGE=""
YES=0
DRY=0
NO_PATH=0

while [ $# -gt 0 ]; do
  case "$1" in
    --lang) LANGUAGE="${2:-}"; shift 2 ;;
    --source) SOURCE="${2:-}"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    --dry-run) DRY=1; shift ;;
    --no-path-update) NO_PATH=1; shift ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
done

if [ -z "$LANGUAGE" ]; then
  case "${KDX_LOCALE:-${LC_ALL:-${LANG:-}}}" in sk*) LANGUAGE=sk ;; *) LANGUAGE=en ;; esac
fi

# Colour only on a terminal that wants it.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RULE='\033[90m'; C_STEP='\033[36m'; C_OK='\033[32m'; C_ASK='\033[33m'
  C_BAD='\033[31m'; C_INFO='\033[96m'; C_TITLE='\033[35m'; C_OFF='\033[0m'
else
  C_RULE=''; C_STEP=''; C_OK=''; C_ASK=''; C_BAD=''; C_INFO=''; C_TITLE=''; C_OFF=''
fi

msg() {
  # msg KEY -> text in the chosen language
  case "$LANGUAGE:$1" in
    sk:title) echo "Jadro Korodexios - inštalácia" ;;
    sk:intro) echo "Štyri krátke kroky. Nič sa nenainštaluje bez opýtania a môžeš kedykoľvek skončiť (Ctrl+C)." ;;
    sk:s1) echo "Krok 1 zo 4 - kontrola počítača" ;;
    sk:s2) echo "Krok 2 zo 4 - inštalácia" ;;
    sk:s3) echo "Krok 3 zo 4 - overenie" ;;
    sk:s4) echo "Krok 4 zo 4 - tvoj prvý projekt" ;;
    sk:uvok) echo "uv nájdený:" ;;
    sk:pyok) echo "Python nájdený - použije sa:" ;;
    sk:pyno) echo "Python 3.11+ v počítači nie je - uv ho pre nástroj stiahne (nič sa nemení v celom systéme)." ;;
    sk:gitok) echo "git nájdený:" ;;
    sk:gitno) echo "git sa nenašiel - treba ho len na inštaláciu z git tagu. Nainštaluj ho alebo inštaluj z lokálneho priečinka." ;;
    sk:gitnot) echo "git: pri lokálnej inštalácii netreba" ;;
    sk:kdxok) echo "kdx je už nainštalovaný - tento beh ho aktualizuje:" ;;
    sk:kdxno) echo "kdx ešte nie je nainštalovaný - nainštaluje sa" ;;
    sk:uvno) echo "uv (inštalátor nástrojov pre Python) sa nenašiel." ;;
    sk:uvask) echo "Nainštalovať uv teraz z astral.sh (len pre tohto používateľa, bez sudo)?" ;;
    sk:uvskip) echo "Bez uv inštalátor nemôže pokračovať. Nainštaluj ho z https://docs.astral.sh/uv/ a spusti ma znova." ;;
    sk:src) echo "Inštalujem z:" ;;
    sk:doing) echo "Inštalujem príkaz 'kdx' (môže to trvať minútu)..." ;;
    sk:done) echo "Hotovo. Príkaz sa volá 'kdx'." ;;
    sk:path) echo "Ak 'kdx' ešte nenájde, otvor nový terminál - zmena PATH platí až v nových reláciách." ;;
    sk:ver) echo "Verzia:" ;;
    sk:doctor) echo "Kontrolujem, že kdx štartuje (moduly, ktoré našiel):" ;;
    sk:initask) echo "Vytvoriť teraz prvý projekt? (opýta sa pár otázok a vytvorí priečinok)" ;;
    sk:nameask) echo "Názov projektu (písmená, čísla, podčiarkovník)" ;;
    sk:initwhy) echo "Neskôr to zvládneš aj sám: kdx init moj-projekt" ;;
    sk:next) echo "Čo ďalej" ;;
    sk:n1) echo "kdx --help            všetky príkazy, jednoducho vysvetlené" ;;
    sk:n2) echo "kdx playbook         pracovná príručka pre AI agentov" ;;
    sk:n3) echo "kdx locale set sk     slovenčina v nápovede a hláseniach" ;;
    sk:n4) echo "kdx wizard            vedené menu ku každému príkazu" ;;
    sk:dry) echo "Skúšobný beh: nič sa nezmenilo." ;;
    sk:yn) echo "[A/n]" ;;
    en:title) echo "Korodexios core - installation" ;;
    en:intro) echo "Four short steps. Nothing is installed without asking, and you can stop at any time (Ctrl+C)." ;;
    en:s1) echo "Step 1 of 4 - check this computer" ;;
    en:s2) echo "Step 2 of 4 - install" ;;
    en:s3) echo "Step 3 of 4 - verify" ;;
    en:s4) echo "Step 4 of 4 - your first project" ;;
    en:uvok) echo "uv found:" ;;
    en:pyok) echo "Python found - will be used:" ;;
    en:pyno) echo "No Python 3.11+ on this computer - uv will fetch one for the tool (nothing system-wide changes)." ;;
    en:gitok) echo "git found:" ;;
    en:gitno) echo "git not found - needed only to install from the git tag. Install it or install from a local folder." ;;
    en:gitnot) echo "git: not needed for a local install" ;;
    en:kdxok) echo "kdx is already installed - this run will update it:" ;;
    en:kdxno) echo "kdx is not installed yet - it will be installed" ;;
    en:uvno) echo "uv (the Python tool installer) was not found." ;;
    en:uvask) echo "Install uv now from astral.sh (current user only, no sudo needed)?" ;;
    en:uvskip) echo "Without uv this installer cannot continue. Install it from https://docs.astral.sh/uv/ and run this again." ;;
    en:src) echo "Installing from:" ;;
    en:doing) echo "Installing the 'kdx' command (this can take a minute)..." ;;
    en:done) echo "Installed. The command is 'kdx'." ;;
    en:path) echo "Open a new terminal if 'kdx' is not found yet - PATH changes only apply to new sessions." ;;
    en:ver) echo "Version:" ;;
    en:doctor) echo "Checking that kdx starts (modules it found):" ;;
    en:initask) echo "Create a first project now? (asks a few questions, writes a folder)" ;;
    en:nameask) echo "Name of the project (letters, digits, underscore)" ;;
    en:initwhy) echo "Later you can do it yourself: kdx init my-project" ;;
    en:next) echo "Next steps" ;;
    en:n1) echo "kdx --help            all commands, in plain words" ;;
    en:n2) echo "kdx playbook         the working guide for AI agents" ;;
    en:n3) echo "kdx locale set sk     use Slovak for help and messages" ;;
    en:n4) echo "kdx wizard            a guided menu for every command" ;;
    en:dry) echo "Dry run: nothing was changed." ;;
    en:yn) echo "[Y/n]" ;;
  esac
}

rule() { printf '%b%s%b\n' "$C_RULE" "----------------------------------------------------------------" "$C_OFF"; }
step() { printf "\n"; rule; printf "${C_STEP} %s${C_OFF}\n" "$1"; rule; }
ok()   { printf "${C_OK}[✓] %s${C_OFF}\n" "$*"; }
bad()  { printf "${C_BAD}[!] %s${C_OFF}\n" "$*" >&2; }
info() { printf "${C_INFO}[i] %s${C_OFF}\n" "$*"; }

confirm() {
  if [ "$YES" -eq 1 ]; then printf "${C_ASK}[?] %s${C_OFF} yes\n" "$1"; return 0; fi
  if [ "$DRY" -eq 1 ]; then info "$1 (dry run: not asked)"; return 1; fi
  if [ ! -t 0 ]; then info "$1 (no terminal: skipped, pass --yes to accept)"; return 1; fi
  printf "${C_ASK}[?] %s %s ${C_OFF}" "$1" "$(msg yn)"
  read -r answer
  case "$answer" in ""|y|Y|a|A|yes|ano) return 0 ;; *) return 1 ;; esac
}

run() {
  printf "${C_RULE}    > %s${C_OFF}\n" "$*"
  [ "$DRY" -eq 1 ] && return 0
  "$@"
}

printf "\n  ${C_TITLE}◆ %s${C_OFF}\n  %s\n" "$(msg title)" "$(msg intro)"

step "$(msg s1)"
# What is already here is reused; only what is missing is installed.
py_found=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1      && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    py_found=$(command -v "$candidate"); break
  fi
done
if [ -n "$py_found" ]; then ok "$(msg pyok) $py_found"; else info "$(msg pyno)"; fi
here=$(cd "$(dirname "$0")" && pwd)
if [ -z "$SOURCE" ] && [ ! -f "$here/pyproject.toml" ] || case "$SOURCE" in *git+*) true ;; *) false ;; esac; then
  if command -v git >/dev/null 2>&1; then ok "$(msg gitok) $(command -v git)"; else bad "$(msg gitno)"; fi
else
  info "$(msg gitnot)"
fi
if command -v kdx >/dev/null 2>&1; then ok "$(msg kdxok) $(command -v kdx)"; else info "$(msg kdxno)"; fi
if command -v uv >/dev/null 2>&1; then
  ok "$(msg uvok) $(command -v uv)"
else
  bad "$(msg uvno)"
  if confirm "$(msg uvask)"; then
    printf "${C_RULE}    > curl -LsSf https://astral.sh/uv/install.sh | sh${C_OFF}\n"
    if [ "$DRY" -eq 0 ]; then
      curl -LsSf https://astral.sh/uv/install.sh | sh
      PATH="$HOME/.local/bin:$PATH"; export PATH
    fi
  fi
  if ! command -v uv >/dev/null 2>&1 && [ "$DRY" -eq 0 ]; then bad "$(msg uvskip)"; exit 1; fi
fi

step "$(msg s2)"
if [ -z "$SOURCE" ]; then
  here=$(cd "$(dirname "$0")" && pwd)
  if [ -f "$here/pyproject.toml" ]; then SOURCE="$here"; else SOURCE="$RELEASE_SOURCE"; fi
fi
info "$(msg src) $SOURCE"
info "$(msg doing)"
run uv tool install --force "$SOURCE"
[ "$DRY" -eq 0 ] && { [ "$NO_PATH" -eq 1 ] || uv tool update-shell >/dev/null 2>&1 || true; bin=$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin"); PATH="$bin:$PATH"; export PATH; }
ok "$(msg done)"
info "$(msg path)"

step "$(msg s3)"
if [ "$DRY" -eq 0 ]; then
  ok "$(msg ver) $(kdx version)"
  info "$(msg doctor)"
  scratch=$(mktemp -d)  # throw-away folder: the check leaves nothing in your current one
  (cd "$scratch" && kdx --locale "$LANGUAGE" --no-log-file --home "$scratch" modules list) || true
  rm -rf "$scratch"
else
  run kdx version
  run kdx modules list
fi

step "$(msg s4)"
if confirm "$(msg initask)"; then
  name="my_project"
  if [ "$YES" -eq 0 ] && [ -t 0 ]; then
    printf "${C_ASK}[?] %s [%s] ${C_OFF}" "$(msg nameask)" "$name"
    read -r typed
    [ -n "$typed" ] && name="$typed"
  fi
  run kdx --locale "$LANGUAGE" init "$name"
else
  info "$(msg initwhy)"
fi

step "$(msg next)"
for key in n1 n2 n3 n4; do printf "   %s\n" "$(msg "$key")"; done
printf "\n"
[ "$DRY" -eq 1 ] && info "$(msg dry)"
exit 0
