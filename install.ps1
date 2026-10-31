<#
.SYNOPSIS
  Guided installer for the Korodexios core (kdx) on Windows.

.DESCRIPTION
  Walks through four clearly separated steps - check, install, verify, first project -
  with colour, plain explanations, and nothing hidden. It asks before it installs uv or
  creates anything, and it never changes system settings.

.PARAMETER Source
  What to install. Default: this folder when run from the repository, otherwise the
  released git tag.

.PARAMETER Language
  en or sk. Default: your Windows display language, else en.

.PARAMETER Yes
  Answer yes to every question (for scripts).

.PARAMETER DryRun
  Show every step and command, run nothing.

.EXAMPLE
  .\install.ps1
  .\install.ps1 -Language sk
  .\install.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string]$Source = "",
    [ValidateSet("", "en", "sk")]
    [string]$Language = "",
    [switch]$Yes,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch { }  # so ✓ and diacritics display
$ReleaseSource = "korodexios-core @ git+https://github.com/korodexios/kdx-core.git@v0.4.0"

if (-not $Language) {
    $Language = if ((Get-UICulture).TwoLetterISOLanguageName -eq "sk") { "sk" } else { "en" }
}

$Text = @{
    en = @{
        title   = "Korodexios core - installation"
        intro   = "Four short steps. Nothing is installed without asking, and you can stop at any time (Ctrl+C)."
        s1      = "Step 1 of 4 - check this computer"
        s2      = "Step 2 of 4 - install"
        s3      = "Step 3 of 4 - verify"
        s4      = "Step 4 of 4 - your first project"
        uvok    = "uv found: {0}"
        uvno    = "uv (the Python tool installer) was not found."
        uvask   = "Install uv now from astral.sh (current user only, no admin needed)?"
        uvskip  = "Without uv this installer cannot continue. Install it from https://docs.astral.sh/uv/ and run this again."
        src     = "Installing from: {0}"
        doing   = "Installing the 'kdx' command (this can take a minute)..."
        done    = "Installed. The command is 'kdx'."
        path    = "Open a new terminal if 'kdx' is not found yet - the PATH changes only apply to new windows."
        ver     = "Version: {0}"
        doctor  = "Running 'kdx doctor' - it explains the state of your setup:"
        initask = "Create a first project now? (asks a few questions, writes a folder)"
        initwhy = "Later you can do it yourself: kdx init my-project"
        next    = "Next steps"
        n1      = "kdx --help            all commands, in plain words"
        n2      = "kdx conventions       the rules for building a project (add --lang sk for Slovak)"
        n3      = "kdx locale set sk     use Slovak for help and messages"
        n4      = "kdx wizard            a guided menu for every command"
        dry     = "Dry run: nothing was changed."
        fail    = "Something went wrong: {0}"
        yn      = "[Y/n]"
    }
    sk = @{
        title   = "Jadro Korodexios - inštalácia"
        intro   = "Štyri krátke kroky. Nič sa nenainštaluje bez opýtania a môžeš kedykoľvek skončiť (Ctrl+C)."
        s1      = "Krok 1 zo 4 - kontrola počítača"
        s2      = "Krok 2 zo 4 - inštalácia"
        s3      = "Krok 3 zo 4 - overenie"
        s4      = "Krok 4 zo 4 - tvoj prvý projekt"
        uvok    = "uv nájdený: {0}"
        uvno    = "uv (inštalátor nástrojov pre Python) sa nenašiel."
        uvask   = "Nainštalovať uv teraz z astral.sh (len pre tohto používateľa, bez admina)?"
        uvskip  = "Bez uv inštalátor nemôže pokračovať. Nainštaluj ho z https://docs.astral.sh/uv/ a spusti ma znova."
        src     = "Inštalujem z: {0}"
        doing   = "Inštalujem príkaz 'kdx' (môže to trvať minútu)..."
        done    = "Hotovo. Príkaz sa volá 'kdx'."
        path    = "Ak 'kdx' ešte nenájde, otvor nový terminál - zmena PATH platí až v nových oknách."
        ver     = "Verzia: {0}"
        doctor  = "Spúšťam 'kdx doctor' - vysvetlí stav tvojho nastavenia:"
        initask = "Vytvoriť teraz prvý projekt? (opýta sa pár otázok a vytvorí priečinok)"
        initwhy = "Neskôr to zvládneš aj sám: kdx init moj-projekt"
        next    = "Čo ďalej"
        n1      = "kdx --help            všetky príkazy, jednoducho vysvetlené"
        n2      = "kdx conventions       pravidlá stavby projektu (--lang en pre angličtinu)"
        n3      = "kdx locale set sk     slovenčina v nápovede a hláseniach"
        n4      = "kdx wizard            vedené menu ku každému príkazu"
        dry     = "Skúšobný beh: nič sa nezmenilo."
        fail    = "Niečo sa pokazilo: {0}"
        yn      = "[A/n]"
    }
}
$T = $Text[$Language]

function Write-Rule { Write-Host ("-" * 64) -ForegroundColor DarkGray }
function Write-Step([string]$Title) { Write-Host ""; Write-Rule; Write-Host " $Title" -ForegroundColor Cyan; Write-Rule }
function Write-Ok([string]$Message)   { Write-Host "[✓] $Message" -ForegroundColor Green }
function Write-Ask([string]$Message)  { Write-Host "[?] $Message" -ForegroundColor Yellow -NoNewline }
function Write-Warn([string]$Message) { Write-Host "[!] $Message" -ForegroundColor Red }
function Write-Info([string]$Message) { Write-Host "[i] $Message" -ForegroundColor DarkCyan }

function Confirm-Step([string]$Question) {
    if ($Yes) { Write-Ask "$Question "; Write-Host "yes" -ForegroundColor Green; return $true }
    if ($DryRun) { Write-Info "$Question (dry run: not asked)"; return $false }
    Write-Ask "$Question $($T.yn) "
    $answer = Read-Host
    return ($answer -eq "" -or $answer -match "^(y|a)")
}

function Invoke-Step([string]$Display, [scriptblock]$Action) {
    Write-Host "    > $Display" -ForegroundColor DarkGray
    if ($DryRun) { return }
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "$Display (exit $LASTEXITCODE)" }
}

try {
    Write-Host ""
    Write-Host "  ◆ $($T.title)" -ForegroundColor Magenta
    Write-Host "  $($T.intro)" -ForegroundColor Gray

    # --- 1. check -----------------------------------------------------------
    Write-Step $T.s1
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        Write-Ok ($T.uvok -f $uv.Source)
    } else {
        Write-Warn $T.uvno
        if (Confirm-Step $T.uvask) {
            Invoke-Step "irm https://astral.sh/uv/install.ps1 | iex" { Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression; $global:LASTEXITCODE = 0 }
            if (-not $DryRun) {
                $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
                $uv = Get-Command uv -ErrorAction SilentlyContinue
            }
        }
        if (-not $uv -and -not $DryRun) { Write-Warn $T.uvskip; exit 1 }
    }

    # --- 2. install ---------------------------------------------------------
    Write-Step $T.s2
    if (-not $Source) {
        $here = Split-Path -Parent $MyInvocation.MyCommand.Path
        $Source = if (Test-Path (Join-Path $here "pyproject.toml")) { $here } else { $ReleaseSource }
    }
    Write-Info ($T.src -f $Source)
    Write-Info $T.doing
    Invoke-Step "uv tool install --force `"$Source`"" { uv tool install --force "$Source" }
    if (-not $DryRun) { Invoke-Step "uv tool update-shell" { uv tool update-shell; $global:LASTEXITCODE = 0 } }
    Write-Ok $T.done
    Write-Info $T.path

    # --- 3. verify ----------------------------------------------------------
    Write-Step $T.s3
    if (-not $DryRun) {
        $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
        Write-Ok ($T.ver -f ((& kdx version) -join " "))
        Write-Info $T.doctor
        & kdx --locale $Language doctor
    } else {
        Invoke-Step "kdx version" {}
        Invoke-Step "kdx doctor" {}
    }

    # --- 4. first project ---------------------------------------------------
    Write-Step $T.s4
    if (Confirm-Step $T.initask) {
        Invoke-Step "kdx init" { & kdx --locale $Language init }
    } else {
        Write-Info $T.initwhy
    }

    Write-Step $T.next
    foreach ($key in "n1", "n2", "n3", "n4") { Write-Host "   $($T[$key])" -ForegroundColor White }
    Write-Host ""
    if ($DryRun) { Write-Info $T.dry }
}
catch {
    Write-Warn ($T.fail -f $_.Exception.Message)
    exit 1
}
