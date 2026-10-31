# Pravidlá a konvencie projektu

Pravidlá, ktoré dodržiava každý projekt Korodexios. Sú napísané tak, aby ich vedel
použiť človek, ktorý neprogramuje, aj AI agent. Zámerne stručné: ak tu pravidlo nie je,
najprv sa opýtaj, nevymýšľaj ho. Anglická verzia je `conventions.md`
(`kdx conventions --lang en`).

**Ako to používať.** Drž tento súbor v projekte (`docs/CONVENTIONS.md`). Na začiatku
každej relácie povedz AI agentovi: *„Najprv prečítaj AGENTS.md a docs/CONVENTIONS.md.“*

## 1. Myšlienka v piatich riadkoch

1. Každý projekt stojí na jadre Korodexios (`kdx`): malom, stabilnom šasi.
2. Projekt je **veľa malých modulov** (pluginov), nikdy jeden veľký program.
3. Všetko musí fungovať najprv z terminálu; webová stránka je len doplnok.
4. Nič nie je „hotové“, kým neprejdú automatické kontroly (`make check`).
5. Pri všetkom rizikovom alebo ťažko vrátnom musí najprv povedať áno človek.

## 2. Štruktúra: malé kúsky

AI agenti (aj ľudia) robia v malých kúskoch kódu menej chýb, preto je veľkosť pravidlom:

| Čo | Limit | Ak je väčšie |
| --- | --- | --- |
| funkcia | približne 40 riadkov | rozdeliť |
| súbor | približne 400 riadkov | rozdeliť na dva moduly |
| modul | jedna úloha, opísaná jednou vetou | spraviť druhý modul |
| argumenty funkcie | najviac 5 | zoskupiť do malej dátovej triedy |

Pravidlá modulov:

- Modul je priečinok `src/<projekt>/modules/<názov>/` so súbormi `module.py` a
  `kdx_module.toml` (manifest). Jadro ho nájde samo; nikto neupravuje centrálny zoznam.
- Moduly **sa navzájom neimportujú**. Komunikujú cez zbernicu udalostí, register alebo
  riadiacu rovinu. Vďaka tomu sa každý modul dá odstrániť, vymeniť alebo testovať samostatne.
- Modul deklaruje svoje nastavenia (`config_schema`), číta ich len cez jadro a všetko,
  čo spustil, uvoľní v `stop()`.
- Modul píše diagnostiku na stderr a nikdy nevypisuje na stdout; pre ľudí píše len
  vrstva CLI.
- Žiadne atrapy: žiadne telá s `TODO`, prázdne `pass`, vymyslené návratové hodnoty. Ak
  vetva existuje, beží a má test.

## 3. Začiatok projektu od nuly

```bash
kdx init moj-projekt        # opýta sa pár otázok a vytvorí celú kostru
cd moj-projekt
uv sync --extra dev         # alebo: pip install -e ".[dev]"
make check                  # pred prvou zmenou musí byť zelené
kdx modules create fakturacia   # jeden nový modul (priečinok s module.py a manifestom)
kdx doctor                  # projekt sám vysvetlí svoj stav
```

Kostra obsahuje: `AGENTS.md` (zmluva pre agentov), `docs/` (architektúra a tieto
pravidlá), `kdx.yml` (konfigurácia), `tests/`, `.github/workflows/ci.yml` (automatické
kontroly pri každej zmene) a `Makefile`.

## 4. Ako pracujeme: špecifikácia, test, kód, kontrola

Tieto skratky znamenajú tú istú myšlienku: **rozhodni pred stavbou, over po stavbe.**

| Skratka | Čo znamená jednoducho | Ako to používame |
| --- | --- | --- |
| **SDD** (špecifikáciou riadený vývoj) | Najprv napíš, čo chceš, až potom kód. | Funkcia začína pol stranou špecifikácie v `docs/specs/NNN-nazov.md`: cieľ, čo **nerobí**, ako sa správa a podľa čoho poznáme, že funguje (kritériá prijatia). |
| **TDD** (testom riadený vývoj) | Najprv napíš test, ktorý zlyhá, potom kód, ktorý ho splní. | Červená (test padá) → zelená (najmenší kód prejde) → upratať. Agentovi povedz: „napíš najprv test, ukáž, že padá, a potom implementuj.“ |
| **BDD** (správaním riadený vývoj) | Správanie opíš ako „dané / keď / potom“. | Kritériá a názvy testov znejú ako vety: `test_zastavenie_ukonceneho_jobu_to_povie`. |
| **ADR** (záznam rozhodnutia) | Poznámka, prečo sme zvolili X. | Jeden krátky súbor na veľké rozhodnutie v `docs/decisions/NNN-nazov.md`: súvislosti, rozhodnutie, dôsledky. Starý nikdy neprepisuj; napíš nový, ktorý ho nahradí. |
| **CI** | Počítač spustí všetky kontroly pri každej zmene. | `.github/workflows/ci.yml` spúšťa `make check` na Linuxe a Windowse pri každom pushi a pull requeste. |
| **CD** | Počítač vydá verziu, keď kontroly prejdú. | Vydanie je git tag (`v1.2.0`) na zelenom `main`; workflow z tagu zostaví balík. Nič sa nenasadzuje z notebooku. |

Nepotrebuješ doménovo riadený návrh, mikroslužby ani iné veľké metódy. Stačia moduly,
špecifikácie, testy a automatické kontroly.

**Cyklus pri každej zmene** (jedna zmena = jeden malý cieľ = jeden commit alebo pull request):

1. Povedz cieľ jednou vetou. Ak by sa dotýkal viac než pár modulov, rozdeľ ho.
2. Napíš alebo uprav špecifikáciu a kritériá prijatia.
3. Napíš test, ktorý zlyhá.
4. Píš kód, kým test neprejde.
5. Spusti `make check` (lint, prísne typy, testy, minimálne pokrytie). Oprav a opakuj.
6. Uprav dokumentáciu, `CHANGELOG.md` a preklady, ak sa zmenil text pre ľudí.
7. Commitni s jasnou správou; otvor pull request; človek si prečíta zmeny a zlúči ich.

## 5. Definícia „hotovo“

Zmena je hotová, len ak platí **všetko** z toho:

- [ ] `make check` prejde (ruff, mypy `--strict`, pytest, pokrytie nie nižšie).
- [ ] Existuje test, ktorý bez tejto zmeny zlyhá.
- [ ] Funguje z CLI (a s `--json`, ak by to použil skript).
- [ ] Nápoveda a hlásenia sú jasné, povedia, čo robiť ďalej, a dajú sa prekladať.
- [ ] V kóde, logoch ani gite nie je tajomstvo, token, heslo ani osobný údaj.
- [ ] Dokumentácia a `CHANGELOG.md` sú aktuálne; verzia sa zvyšuje len pri vydaní.

## 6. Bezpečnostné pravidlá

- **Tajomstvá** patria do premenných prostredia alebo `.env` (nikdy do gitu;
  `.env.example` uvádza len mená). Logy, chyby ani výstup `--json` ich neobsahujú.
- **Predvolene zatvorené**: počúvaj na `127.0.0.1`, vyžaduj token, každému daj najnižšiu
  rolu, ktorá stačí (viewer < operator < admin). Vypnutie ochrany musí byť výslovné.
- **Kontroluj každý vstup** na okraji (argumenty CLI, súbory, sieť). Nikdy nesklady
  príkazy shellu ani cesty k súborom z nekontrolovaného textu; nepoužívaj `eval`/`exec`.
- **Málo závislostí**: najprv štandardná knižnica; nová závislosť potrebuje písomný
  dôvod, pripnutú verziu a kontrolu cez `pip-audit` (alebo `uv pip audit`).
- **Najmenšie prekvapenie na disku**: zapisuj len v projekte (`var/`, `data/`); nič, čo
  si nevytvoril, nemaž ani nepreprisuj bez opýtania.
- **Aktualizácie**: lock súbor je v gite; závislosti aktualizuj v samostatnej malej zmene.
- **Každá funkcia** má vo svojej špecifikácii vetu: „Čo sa môže pokaziť a kto by to
  mohol zneužiť?“

## 7. Najprv sa opýtaj (musí povedať áno človek)

AI agenti sa musia zastaviť a opýtať pred: `git push`, vytvorením tagu vydania, zlúčením
do `main`, mazaním súborov alebo dát, zmenou prihlasovacích údajov alebo oprávnení,
inštaláciou služby na počítači, pridaním závislosti, míňaním peňazí alebo odoslaním
dát externej službe. Súhlas s jednou vecou neplatí pre ďalšiu.

## 8. Práca s AI agentmi

- Zadávaj **jednu malú úlohu naraz**. Malá úloha má jasný koniec („pridaj `--json` do
  `fakturacia list`“), nie smer („vylepši fakturáciu“).
- Agenta vždy nasmeruj na `AGENTS.md` a tento súbor; obidva drž krátke a aktuálne.
- Ak sa zmena dotýka viac než jedného modulu, nechaj si najprv ukázať plán.
- Žiadaj, aby agent **ukázal padajúci test a potom prechádzajúci beh**, nie „malo by
  to fungovať“.
- Čítaj zmeny. Ak im nerozumieš, požiadaj o menšie alebo o vysvetlenie jednoducho. Vždy
  môžeš povedať „vysvetli to jednoducho“ alebo „odpovedaj po slovensky“.
- Na novú úlohu začni novú reláciu; dlhé relácie sa rozchádzajú. Čo musí zostať
  (rozhodnutia, pravidlá), patrí do súborov v repozitári, nie do chatu.
- Agenti commitujú len do vetvy funkcie. O pushi, tagu a zlúčení rozhoduje človek.

## 9. Slová a vzhľad (najprv CLI)

- Každý príkaz má `--help`, ktorý vysvetlí jednoducho a s príkladom.
- Chyby povedia, čo sa stalo a čo robiť ďalej, na jeden-dva riadky; žiadne výpisy
  chýb, kým nezadáš `-v`.
- Výstup pre ľudí ide na stdout, diagnostika na stderr, strojový výstup cez `--json`.
- Stavové značky: `[✓]` hotovo, `[?]` otázka, `[!]` problém, `[i]` informácia. Farba
  pomáha, ale nikdy nie je jediným signálom (musí byť čitateľné bez farieb aj v rúre).
- Príkazy a prepínače zostávajú všade po anglicky; text pre ľudí sa prekladá
  (`kdx locale`). Jazyk pridáš podľa `docs/TRANSLATING.md`.
- Všetko, čo sa pýta, má predvolenú hodnotu v `[zátvorkách]`, prijme Enter a pre skripty
  má `--yes`. Nikdy nečakaj naprázdno, keď nie je terminál.

## 10. Návyky s gitom

- Jedna funkcia = jedna vetva (`feature/kratky-nazov`); `main` vždy prejde `make check`.
- Správy commitov začínajú slovesom: `add:`, `fix:`, `change:`, `docs:`, `release:`.
- Malé commity, z ktorých každý prejde kontrolami. Nikdy nekomituj `var/`, `.env` ani
  výsledky zostavenia.
- Verzie sledujú sémantické verzovanie: `MAJOR.MINOR.PATCH` (zlomová zmena, funkcia, oprava).

## 11. Zoznam na deň nula nového projektu

1. `kdx init moj-projekt` a `make check` je zelené.
2. Napíš `docs/specs/001-<prva-funkcia>.md` (cieľ, čo nerobí, správanie, kritériá).
3. Vytvor repozitár a chráň `main` (žiadny priamy push, CI musí prejsť).
4. Tajomstvá do CI pridaj len ak naozaj treba; na `make check` žiadne netreba.
5. Rozhodni, aké jazyky chceš (`kdx locale add xx`).
6. Prvý modul začni testom.
