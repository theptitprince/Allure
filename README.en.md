<div align="center">

# Allure — Polar generator

**Speed polar generator from NMEA0183 sentences, with a rolling recording buffer, live monitoring and a cumulative store.**

![Version](https://img.shields.io/badge/version-2.2a-2ea44f)
![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white)
![tkinter](https://img.shields.io/badge/interface-tkinter-3776AB?logo=python&logoColor=white)
![matplotlib](https://img.shields.io/badge/plots-matplotlib-11557C)
![NMEA 0183](https://img.shields.io/badge/input-NMEA%200183%20over%20UDP-0A6E8A)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6?logo=windows&logoColor=white)
![License](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)

</div>

[Français](README.md) · **English**

> The program's interface is in French; its labels are quoted below as they appear on screen.

---

> [!NOTE]
> **Windows first; Python 3.8 or later, with tkinter and matplotlib.** The only pitfall of the
> installation is the `tcl/tk and IDLE` box in the Python installer (section 1);
> `installer_dependances.bat` then installs the rest. For a computer without Python, `construire_exe.bat`
> builds a standalone `Allure.exe` (section 3). Your measurements accumulate in the `donnees/` folder,
> your most valuable asset: do not delete it, and remember to export a backup of it (section 4).

**Who it is for.** For the crew of a sailing vessel whose gateway broadcasts
its NMEA0183 sentences on the onboard network (UDP), and who want to know
what the boat *really does*: Allure listens to the sentences, records runs
annotated by sail plan, accumulates them outing after outing in a store, and
derives from them polars per configuration, a max polar for routing
(`.pol`, `.csv`, TimeZero) and a sail guide.

Current version: 2.2a — the release history is in [CHANGELOG.md](CHANGELOG.md) (in French).

## Contents

- [1. Installation](#1-installation)
- [2. The files](#2-the-files)
- [3. Launching](#3-launching)
- [4. What the program creates alongside itself](#4-what-the-program-creates-alongside-itself)
- [5. How the interface is organized](#5-how-the-interface-is-organized)
- [6. The Entrepôt step in detail](#6-the-entrepôt-step-in-detail)
- [7. The Polaires step in detail](#7-the-polaires-step-in-detail)
- [8. Sources and acquisition](#8-sources-and-acquisition)
- [9. Troubleshooting](#9-troubleshooting)
- [10. License](#10-license)

---

## 1. Installation

### Python

You need **Python 3.8 or later**, with **tkinter**.

On Windows, install Python from <https://www.python.org/downloads/> and
**be sure to check two boxes** in the installer:

- `Add Python to PATH` (on the first screen)
- `tcl/tk and IDLE` (on the "Optional Features" screen)

Without the second one, the application cannot open: tkinter is what draws
the whole interface. It is the only pitfall of the installation.

To check, open PowerShell and type:

```powershell
python --version
python -m tkinter
```

The first command must display a version number, the second must open a
small test window — close it.

### The easiest way: `installer_dependances.bat`

Once Python is installed, double-click **`installer_dependances.bat`**, in
the Allure folder (see section 2). It installs for the `python` command
— the one this README uses to launch Allure; failing that, it falls back on
the `py -3` launcher — and displays the path of that interpreter; it checks
tkinter, installs **matplotlib** (essential) then **pystray** and
**pillow** (optional, see below), and ends with a summary. If it fails, it
says why — and the *Offline installation* section below takes over.

Does your launch shortcut point to a different Python (section 3)? Then
install for that one: `"C:\Chemin\vers\python.exe" -m pip install matplotlib`.

The commands that follow do the same thing by hand.

### The only library to install

```powershell
python -m pip install matplotlib
```

That's all — matplotlib's dependencies are installed automatically along
with it.

To check:

```powershell
python -c "import matplotlib, tkinter; print('tout est en place')"
```

### Optional: the notification area

To have the window's close cross **tuck** the application away next to the
clock instead of closing it (it then keeps running, rolling buffer and
recording included; actually shutting it down is done by right-clicking the
icon → *Quitter Allure* (quit Allure)):

```powershell
python -m pip install pystray pillow
```

Without these two libraries, the cross closes normally — nothing else
changes. The option is set in Paramètres (settings) → *Affichage & fenêtre*
(display & window).

### Offline installation

When `pip` cannot reach the internet — no network on board, a proxy, or a
firewall and antivirus that forbid network access to `python.exe` (the
`WinError 10013` error is the telltale sign) — you download the packages
elsewhere and install them from a folder.

**The safe method.** On a **64-bit Windows** PC connected to the internet,
with **the same version of Python** (same `3.XY`: `python --version` on
both; for the bitness, `python -c "import platform; print(platform.architecture()[0])"`,
or the first lines displayed by `installer_dependances.bat`: path, version
and bitness of the Python used):

```powershell
python -m pip download matplotlib pystray pillow -d C:\allure_paquets
```

Copy the `C:\allure_paquets` folder to the onboard PC (USB drive), then:

```powershell
python -m pip install --no-index --find-links C:\allure_paquets matplotlib pystray pillow
```

`--no-index` forbids pip from going to the internet: everything comes from
the folder. `pip download` has the advantage of fetching **all** the
dependencies in one go.

**By hand, file by file.** Downloading matplotlib's own file is not enough:
it depends on numpy, contourpy, cycler, fonttools, kiwisolver, packaging,
pillow, pyparsing, python-dateutil and six (`python -m pip list` shows the
ones you already have). For each of them, on the page
`https://pypi.org/project/<nom>/#files` (`<nom>` being the package name),
take the `.whl` file:

- whose name contains `cp3XY`, where `3.XY` is your Python version
  (`cp314` for Python 3.14), **and** `win_amd64` (64-bit Windows) — never
  `cp314t` (a "free-threaded" variant of Python), nor `win32`, nor
  `win_arm64`;
- or, for packages written in pure Python (cycler, packaging, pyparsing,
  python-dateutil, six), the file ending in `py3-none-any.whl` or
  `py2.py3-none-any.whl`.

Add **pystray** (pure Python as well) if you want the notification area
— otherwise, remove `pystray pillow` from the command. Put all the files in
the same folder, and run the `pip install --no-index --find-links` command
above: if one is missing, pip names it, and installs nothing as long as it
is missing.

The same method applies to PyInstaller, if you build `Allure.exe`
(section 3) — on the connected PC:

```powershell
python -m pip download pyinstaller -d C:\allure_paquets
```

then on the onboard PC:

```powershell
python -m pip install --no-index --find-links C:\allure_paquets pyinstaller
```

---

## 2. The files

Get the repository onto your computer, whichever way you prefer:

- the green **Code → Download ZIP** button on the GitHub page, then unzip
  the archive into a folder (for example `C:\Allure`);
- or, if you have git:

```powershell
git clone https://github.com/theptitprince/Allure.git C:\Allure
```

The program consists of **four files, which must stay together**:

| File | Role |
|---|---|
| `allure.py` | the application: interface, screen logic, and startup |
| `allure_engine.py` | polar computation, with no interface |
| `allure_config.py` | saving of the settings and of the store |
| `allure_buffer.py` | rolling recording buffer |

All four files are required. There is **no** separate launcher:
`allure.py` is launched directly.

Alongside them, files that are not part of the program itself:

| File | Role |
|---|---|
| `installer_dependances.bat` | installs matplotlib (and pystray + pillow, optional) — section 1 |
| `construire_exe.bat` | builds `Allure.exe`, standalone — section 3 |
| `CHANGELOG.md` | the version log (in French) |
| `LICENSE` | the text of the program's license (see "License" at the end of the document) |

---

## 3. Launching

### From PowerShell, or with a shortcut

In PowerShell, in the folder where the files are:

```powershell
cd C:\Allure
python allure.py
```

**For everyday launching, make yourself a shortcut** (right-click on the
Desktop → *New* → *Shortcut*) whose target is:

```text
"C:\Chemin\vers\python.exe" "C:\Allure\allure.py"
```

**And if the black window bothers you**, replace `python.exe` with
`pythonw.exe` in that same target: that is the only change, and the console
disappears. Both go through the same startup code — there are not two
behaviors to learn.

What decides whether there is a console is therefore **the executable**,
and never the name or the extension of the file launched. To know what is
*really* running at a given moment, open the gear icon → *Sauvegardes*
(backups) → **Diagnostic de l'installation** (installation diagnostics): it
shows the running interpreter and says whether there is a console or not.

### The firewall and `pythonw.exe`

**The UDP channels no longer receive anything since you switched to
`pythonw.exe`?** This is the classic Windows trap, and it is not an Allure
fault at all: **the firewall authorizes an EXECUTABLE, not a program**.
Launched by `pythonw.exe`, Allure is a *new* executable in the eyes of the
firewall, even if `python.exe` had always been authorized. The socket opens
normally — no error, no dialog box — and the datagrams are silently dropped
before they arrive.

The live view says so explicitly: the *Réception* (reception) section
shows the actual state of each channel — `à l'écoute, AUCUNE trame (depuis 45 s)`
(listening, NO sentences for 45 s) versus `1234 trame(s), dernière en direct`
(1234 sentence(s), latest one live) — and when an open channel has still
received no sentence after twenty seconds, a banner names the cause, the
executable concerned and where to authorize it: *Paramètres
Windows → Réseau → Pare-feu → Autoriser une application* (Windows Settings
→ Network → Firewall → Allow an app), on a **private** network.

### Errors, even without a console

**And without a console, how do you know an error has occurred?** That is
the trade-off, and it has been taken care of: any unexpected error is
**written to `journaux/erreurs.log`** (with the time, the version and the
full traceback) **and announced by a dialog box**. An application that
closes by itself without saying anything is the worst possible behavior.
The only deliberate exception: an error in the refresh loop, which runs two
and a half times per second — it is logged **only once**, without a dialog
box, otherwise you would be drowned in dialogs; and the loop survives,
because it is what keeps the buffer and the acquisition going. The
*Ouvrir le journal des erreurs* (open the error log) button (gear icon →
*Sauvegardes*, below *Diagnostic de l'installation*) opens this file
without your having to look for it.

### A standalone executable: `Allure.exe`

For a PC on which you do not want to install Python, double-click
**`construire_exe.bat`** on a machine where Allure already runs (matplotlib
installed — see section 1). It installs PyInstaller if it is missing, draws
Allure's icon — a blue disc bearing a white sail, the same logo as in the
notification area and the title bar — and builds **`Allure.exe`**, placed
**next to `allure.py`**.

An executable keeps its folders (`donnees/`, `reglages/`…) next to
**itself**: placed there, `Allure.exe` therefore shares the store and the
settings of `allure.py`. Copied alone to another machine, it creates its
own there — then reimport your backup (gear icon → *Sauvegardes*). A single
file, nothing else to install; it starts a little more slowly than the
script, because it unpacks itself at each launch.

What the exe contains is what is installed on the **build** machine: the
notification area is only included if pystray and pillow are installed
there (section 1, *Optional*) — the script points this out. Installing
pystray afterwards on the machine that *receives* the exe changes nothing:
you have to rebuild.

As with `pythonw.exe` above: `Allure.exe` is a new executable for the
firewall — authorize it on a **private** network at first launch, otherwise
the UDP channels will receive nothing.

---

## 4. What the program creates alongside itself

None of this needs installing: these **four folders** appear by themselves
next to the `.py` files (or next to `Allure.exe`, see section 3).

| Folder | Contents | Can it be deleted? |
|---|---|---|
| `donnees/` | the **cumulative store**, the list of runs, the recycle bin | **No — it is your most valuable asset** |
| `reglages/` | your settings and the state of the interface (last step visited, window size and position…) | Yes (you start again from the default values) |
| `tampon/` | the rolling buffer of raw sentences (~32 MB for 48 h) | Yes |
| `journaux/` | the raw logs of the recordings, `erreurs.log` and `demarrage.log` | Yes — the raw logs are in fact deleted automatically once the recording has been processed (the buffer keeps the same sentences), unless the buffer is disabled or the recording produced no sample |

**Coming from an earlier version?** You have nothing to do. Previous
versions wrote everything in a jumble next to the `.py` files; at first
launch, these files are **moved automatically** into the right folders. A
file is never overwritten: if both exist, the version already in place wins
and the old one stays at the root, where you can examine it.

**Backup.** The simplest way: gear icon → *Sauvegardes* → *Exporter
une sauvegarde* (export a backup). This produces a single `.json` file
containing the store and your runs, to keep wherever you like. On import,
you choose between **fusionner** (merge: only the runs not yet known are
added, with no duplicates) and **remplacer** (replace the current store
entirely — it then goes to the Corbeille, the recycle bin, from which it
can be restored in case of error). Your settings are exported separately
from the same place.

**Changing computer or version.** Copy the program's four files again (or
just `Allure.exe`), then reimport your backup and your settings. A recent
version always reads the data of any earlier version — this is a commitment
of the program.

---

## 5. How the interface is organized

### Three steps and two buttons

Three numbered steps — the normal path of the data:

1. **Acquisition** — the live measurement screen, **at all times**: whether
   you are recording or not, you see what is coming in and whether it is
   usable (a dial with two arrows — true wind and apparent wind, blowing
   toward the boat, their length proportional to the strength —,
   TWA/TWS/STW/SOG in large digits, sampling quality, reception, wind vane
   comparison, raw NMEA stream). It is the screen you leave open on the
   bridge. The **⏱** button at the top right brings you back to it from
   anywhere. *Démarrer l'acquisition* (start acquisition) asks for the
   configuration (sails / engines, and maximum duration) in a single dialog
   box — the previous selection is offered again, so starting again with
   the same sail plan is immediate. *Arrêter* (stop) sends the run
   **straight into the store**; the screen itself does not change: it is
   the state shown in the top bar that changes.
2. **Entrepôt** (store) — your runs accumulate here, with the configuration
   actually used for each one (sails, engines). The table can be sorted by
   clicking the column headers; the box in the *Incluse* (included) column
   includes/excludes a run from the computation with a single click.
   Archive lookup by date (JJ/MM/AAAA, i.e. DD/MM/YYYY) and time (HH:MM): a
   table with one row per minute around the given time, **store AND rolling
   buffer combined** (the minutes that no run covers show up as *(tampon)*
   (buffer) rows, rebuilt from the raw sentences), details on click.
   Date and time are typed **without the separators** — `21082026` and
   `1435` become `21/08/2026` and `14:35` — and the search runs when you
   press Enter. Typing the separators yourself works too: `5/1/2027` gives
   `05/01/2027`. This is also where you find the **import of a .log file**
   (one file, one dialog box, one more run — it is not a mode the
   application switches into), the after-the-fact
   correction of a run's annotation, the **recomputation of a run from the
   buffer**, and the recycle bin.
3. **Polaires** (polars) — the **max polar**: you check the propulsion
   modes to compare — voiles seules (sails only), voiles + 1ME, voiles +
   1SB…, one for each engine configuration in the Paramètres — then their
   sail plans; for each angle/wind cell, the best speed wins. With a single
   sail plan checked, it is that sail plan's own polar. Automatic plot as
   soon as you arrive on the step, sail guide, `.pol` (Expedition), `.csv`
   and TimeZero exports (see section 7).

And, at the top right, **two buttons** that are not steps of the path:
**⏱** (the live measurements) and **⚙** (the settings).

The window opens at a size suited to the screen and to the Windows zoom
level, and at the next launch it recovers the size and position you left it
in.

### The acquisition banner

An **acquisition banner** runs along the top of every page: the state of
the recording, its timer, the sail plan being recorded, and the start/stop
button. A running recording can therefore be seen from the Entrepôt as well
as from the Polaires, and can be stopped without having to go looking for
it.

### Settings

The **Paramètres** open from the **gear icon at the top right** (the step
bar disappears while they are open). Six categories, in the order of the
path: **Voiles & moteurs** (sails & engines), **Sources & tampon** (sources
& buffer: acquisition type, listening channels, analysis and choice of
sources, rolling buffer), **Manœuvres** (maneuvers: the sample filter *and*
the automatic stop — see section 8), **Lissage & table** (smoothing &
table: smoothing of the measurement, polar table, archive sheet),
**Affichage & fenêtre** (damping, theme, notification area),
**Sauvegardes** (store and settings). *Enregistrer les paramètres* (save
the settings) takes you back to the step you came from.

### Light / dark theme

Paramètres → *Affichage & fenêtre*: clair (light), sombre (dark), or
**automatique** (automatic) — dark between sunset and sunrise, computed for
the last GPS position received (GGA/RMC sentences). The change is applied
on the fly, even in the middle of a recording. With no known position, the
automatic mode stays light.

### The mouse wheel

**The wheel scrolls what is under the mouse**, and that alone. A list that
overflows (the runs of the Entrepôt, the sail guide, the sail plans…)
scrolls by itself, to the end and no further: once it reaches its limit, it
does not hand over to the page. A list that fits entirely has nothing to
scroll and lets the page containing it scroll. The wheel over a list's
scrollbar scrolls that list, and the page stays in place. Shift + wheel
keeps its horizontal scrolling. On the polar plot, the wheel zooms (see
section 7).

### Only one instance at a time

Launching Allure a second time on the same computer only displays a
message: two instances would listen to the same UDP channels and write to
the same store. If the window seems impossible to find, look in the
notification area next to the clock — it may be tucked away there (see
*Affichage & fenêtre*).

---

## 6. The Entrepôt step in detail

### The confidence index

A polar does not only state a speed: it also says, implicitly,
*trust me*. Yet not every cell deserves the same trust — a cell built on
200 samples taken during a single ten-minute leg and a cell built on 20
samples spread over eight outings are in no way comparable, and a plain
count would nevertheless give them the same authority. The index —
**A** solide (solid), **B** bonne (good), **C** indicative, **D** fragile —
rests on four observations, from the most important to the least:

1. **independence matters more than number** — two samples ten seconds
   apart on the same leg are not two measurements, they are one measurement
   counted twice (same sea, same trim, same sensor error, same current).
   The samples are therefore grouped into **moments** at least five minutes
   apart, and it is the number of moments that weighs the most;
2. **distinct outings are worth even more** — from one day to the next,
   the sea, the load, the current and the calibration change;
3. **the spread** tells whether the measurements agree with each other;
4. **the number** still counts, but last, and it saturates quickly.

And a safeguard that is worth more than any tuning of weights: a
**cap by number of moments**. Whatever the other factors say, a cell
observed only once will never rise above *indicative*. You do not buy
confidence with redundant measurements — only repetition over time earns
it.

The index can be read at three levels:

- **per cell**, on the Polaires step: the **size of the dots** on the plot
  follows the confidence (that of the cell's winning configuration) and not
  the mere count, and the *une polaire par configuration* (one polar per
  configuration) export (`.csv`) carries the columns *confiance, niveau,
  moments indépendants, sorties, dispersion* (confidence, level,
  independent moments, outings, spread);
- **per run**, in the *Confiance* (confidence) column of the Entrepôt: a
  run is judged on what it **brought back** — how many different cells it
  fed, and with how much material in each. A continuous two-hour recording
  is not penalized (it is a good recording), but two hours spent on the
  same leg are: they teach only one thing. Select a run and the line below
  says **why**;
- **per configuration**, at the top of the Polaires step when a single
  configuration is checked: the *maturité* (maturity) of that polar, the
  number of solid cells, and above all **what has never been measured**
  — "le portant, le vent fort" (downwind, strong wind) — in other words,
  where to go and collect data.

**The index acts on nothing**, unless you ask it to. It rules out no cell,
changes no speed, removes no line from any export. Only the *Incluse* box
of the Entrepôt decides what goes into the computation.

### Automatic sorting on entry

Gear icon → *Lissage & table*, disabled by default: a run whose confidence
is below a threshold can enter the store **unchecked** rather than
automatically included. The **recommended threshold, 40**, is shown just
below the field — it is the boundary between *indicative* (C) and *fragile*
(D). Higher (60, the B/C boundary), you keep only well-distributed runs,
which is useful when the store is already well stocked; lower (20), you
rule out only the really thin runs; at 0 nothing is ever ruled out. Nothing
is deleted or hidden: the run is stored normally, its polar remains
viewable, and the end-of-recording report always says what was decided
**and why**. It is a pre-setting of the box, not censorship — one click
brings the run back in.

### Tout inclure / tout exclure (include all / exclude all)

A master checkbox below the run table toggles all of them at once, with the
count of included runs next to it. Sorting box by box would be clerical
drudgery, and you often want to start again from scratch and keep only a
few.

### Segments: a run is not a block

The measurements themselves are already atomic — one sample every ten
seconds, each one time-stamped. What was too coarse was the **decision**:
including or ruling out two hours at once, when a bad quarter of an hour
(fouled log, engine started without being checked, a passing squall) is
often enough to spoil the rest.

The detail window of a run therefore splits it into **tronçons**
(segments), computed after the fact from the samples already stored —
nothing more was recorded. Three signals delimit them: the **change of
configuration** (an absolute boundary, never erased — two sail plans are
not the same leg); the **tack or change of heading**, detected by a TWA
that sweeps more than 25° in less than two minutes; and the **interruption**
of the measurement. Anything too short is merged back into its neighbor, so
that a two-minute leg does not clutter the list. Each segment shows its
time, its duration, its number of measurements, its tack, its
configuration, its confidence, and **why it starts there** — a boundary
that cannot be explained is a boundary that cannot be trusted.

The *speed* of the change is the essential criterion: a boat that slowly
bears away from 40° to 115° over an hour is not maneuvering, it is changing
its point of sail in the same conditions. It is the same moment, and
splitting it would make no sense — the application does not do it.

Uncheck a segment and the run's polar is redrawn immediately. **Nothing is
deleted**: the samples stay in the store and count again as soon as you
check it again. And the exclusion is saved as a **time range**, not as a
segment number: the splitting is computed, not stored, so refining its
rules one day will not shift any existing exclusion onto other
measurements.

**A pitfall you should be aware of**: splitting does not create
information. Eight segments from the same afternoon are not eight outings
— same sea, same calibration, same current, same crew. Counting them as
independent would inflate the confidence, which is exactly the opposite of
what it exists for. The confidence of a segment coming from a single outing
is therefore **capped**, just as that of a cell observed in a single moment
is: it can be good, it cannot be *solide* on its own.

### The polar of each run

In the Entrepôt, a **double-click** on a run opens the polar derived from
**that run only**. The Polaires step, for its part, plots the cumulative
store — months of measurements combined, so it cannot answer "what did my
outing today give?". The window shows the date, the run's confidence and
one curve per configuration (a run in which you changed sails carries
several). A run excluded from the computation remains viewable — in fact,
it is often in order to decide whether to exclude it or bring it back that
you come to look at it.

### The detail sheet of an archived minute

In the search, a **double-click** on a row opens its full sheet: the polar
quantities, the apparent wind, and the **conditions** — pressure,
temperatures, humidity, course and speed over ground — averaged over a
centered window (10 min by default) read back from the rolling buffer.
Enough to fill in a bridge log after the fact. The choice of quantities and
the averaging duration are set in the gear icon → *Lissage & table*; the
**Cocher ce qui existe à bord** (check what exists on board) button there
checks in one go what your channels actually carry, and unchecks the rest
— a checked box must promise a value, not an empty line.

---

## 7. The Polaires step in detail

### The usable max polar (routing)

Polars per configuration answer *"what can the boat do under this sail
plan?"*; a routing program (qtVlm, Adrena, OpenCPN…), on the other hand,
wants **a single full table**: *"what can the boat do, period"* — it is
then up to the crew to carry the right canvas. The **Polaires** step builds
it directly: for each angle/wind cell, the **best speed** among the checked
sail plans wins — and the page remembers **which one** won, which gives you
the **sail guide** as a bonus ("for this angle and this wind, carry that"),
which you can consult cell by cell (*Guide de voilure* (sail guide) button)
or through the small *Quelle voilure ?* (which sail plan?) finder. **A
single sail plan checked** — double-clicking it in the list is enough — and
it is **its** polar: the per-configuration view is only a special case of
the max polar, it no longer has a screen of its own.

### Propulsion modes, then sail plans — as checkboxes

The *Configurations en concurrence* (competing configurations) frame is
**a single scrolling list**: each propulsion mode has its own box, and its
sail plans follow, indented, when it is checked.

- **Voiles seules** — the configurations without an engine;
- **Voiles + 1ME**, **Voiles + 2ME**, **Voiles + 1SB**… — one box per
  engine configuration declared in **your** Paramètres (gear icon →
  *Voiles & moteurs*), in the same order, **automatically**: add an engine
  code, and its propulsion mode appears. As long as no run has measured it,
  its box is grayed out;
- a combination of engines recorded together (*Voiles + 1ME + 1SB*) also
  has its own.

The number in parentheses counts the sail plans of each propulsion mode.
You can check **as many propulsion modes as you like** — voiles seules
*and* voiles + 1SB, for example — and with all of them checked, sails and
engines compete together (see below). An unchecked propulsion mode
collapses its sail plans and takes them out of the computation; checked
again, it gives them back as you had left them (all checked if none was).
*Tout décocher* (uncheck all) collapses the whole list: you then check the
propulsion mode you want again. The choice of propulsion modes — checked as
well as unchecked — is remembered from one launch to the next; a propulsion
mode that appears later (first run on a new engine code) takes the default
described below, rather than staying out without your knowing it. The
checkmarks on the sail plans, on the other hand, last only for the session.

On this boat — a sail **and** engine cargo vessel — **the engine is part of
the sail plan**: each wind situation calls for a sails/engine
*combination*, not a sail alone. The step therefore opens **by default**
with all propulsion modes checked, and the sail guide also says *when to
switch to the engine*. For a pure sailing polar, check only *Voiles seules*
— or change the default in the gear icon → *Lissage & table*, option
"Le moteur fait partie de la voilure" (the engine is part of the sail
plan): when it is unchecked, only *Voiles seules* is checked on opening;
changing this setting replaces the last remembered choice.

### Readable first

The plot gets the full height of the page, whether the boat has two
configurations or twenty: nothing whose size depends on their number is
stacked above or below it. The configurations live in the left-hand column,
with its own scrollbar and the *Tout cocher* (check all) / *Tout décocher*
buttons. They fit in **a single column** in which each label wraps onto the
next line rather than being cut off, and the column can be **widened with
the mouse**: drag the handle that separates it from the plot — the chosen
width is remembered. Each one carries a **code** (C1, C2…) that does not
change when you check, uncheck or change propulsion mode — unchecking C2
does not rename C3 — and that the guide, the tooltip and a point's detail
sheet reuse. It is the configuration's rank in the store, the best-stocked
first: so it can change when a run is added, included or excluded. The
wind legend sits to the right of the circle; beyond ten wind speeds the
colors repeat, and it is then the **line style** (dashes, then dots) that
distinguishes the curves. The sail guide is displayed **in place of** the
plot, not below it. The exports occupy a footer across the full width, the
options and the search are anchored at the bottom of the left-hand column
— Tk serves the edges before the center, so nothing can be pushed out of
the frame.

### Grid gaps and the `.pol` / `.csv` exports

**Gaps** in the grid are filled by **linear interpolation along the TWA**
(never beyond the first or the last measurement — claiming a speed head to
wind that was never measured would be a lie that the router would take
literally); on the plot, filled dots are measured, hollow dots
interpolated. The **`.pol` export is a full grid**: cells never observed
are 0 there, which a router reads as "zone where the boat does not sail"
(head to wind, typically). The **`.csv`** export gives, for each cell, the
speed, its origin (measured / interpolated) and the winning sail plan; the
*une polaire par configuration* export, on the right of the footer, gives
the spreadsheet the table of each configuration, with its confidence index.

### Derivative-based clipping

Polaires step, left-hand column, disabled by default. A polar is a
**physically smooth** curve: a boat's speed cannot climb by three knots
between 60° and 65°. A cell that claims so does not describe the boat — it
describes surfing a wave, a gust or a push of current, a moment that
routing would take for granted. Clipping limits the **slope** of the curve
(in knots per 10° of TWA, 1.5 recommended) and lowers these peaks.

Three safeguards, each for a specific reason:

- **downward only** — a dip is not suspicious, and raising it would mean
  inventing a performance the boat has never achieved;
- **never at the ends** of the curve, which have only one neighbor:
  limiting them would amount to shaving down the close-hauled or the
  broad-reach end on the strength of a single direction;
- **a peak must stand out on both sides.** Each side is judged on its three
  neighboring cells, the most permissive one prevailing, then it is the
  stricter side that sets the limit. This two-step logic separates three
  situations that a naive criterion confuses: the isolated peak (clipped),
  the neighbor of a **dip** (untouched — without this, a single aberrant
  low measurement used to shave down the whole curve around it), and the
  **plateau** of two peaks that justify each other (both come down).

The number of lowered cells is shown below the control, they carry a
**cross** on the plot, and the `.csv` export marks them `mesure_ecrete`.
Disabled by default, on purpose: filling a gap *adds* where there was
nothing, clipping *modifies* a measured value — it is not the same act, and
the second one has to be asked for.

### Zooming and panning, as on an image

The wheel enlarges the drawing, **dragging** moves around it, a
**double-click** returns to the whole polar. Zooming happens **under the
cursor**: the point you are aiming at stays where it is, otherwise each
notch would push off the screen precisely what you were trying to look at
more closely.

It really is the *plot frame* that grows within the window, not the speed
scale. Narrowing the radius would have been technically simpler, but it
would enlarge neither the graduations nor the gap between two neighboring
close-hauled cells, which is precisely what you want to read more closely.
Here everything grows together and whatever overflows is cropped, exactly
like a photo you enlarge. The zoom **survives a redraw**: checking a
configuration does not throw you back to the initial view. And you cannot
lose the drawing off the screen — panning is bounded.

### Hiding a wind speed

A polar with fifteen curves reads better with five: click a value in the
wind legend, to the right of the circle, and its curve disappears; click
it again, and it comes back. The scale, however, does not move — the other
curves stay in place, so the comparison remains meaningful.

### Details on hover

On the max polar plot, hovering the mouse over a point displays a tooltip:
the cell's angle and wind, speed, origin (measured, interpolated, or
clipped peak), winning sail plan, number of measurements and confidence. A
polar has hundreds of cells; looking up each one in a table breaks the flow
of reading. Away from any point, the tooltip disappears rather than
pointing at a random cell.

### The detail sheet of a point

A **click** on a point of the plot — or on a cell of the sail guide — opens
its full detail sheet: the value retained and where it comes from, its
measurements, its confidence, the runs that fed it, and the configurations
it beat.

### TimeZero export (MaxSea / Nobeltec TZ)

The *Exporter TimeZero* (export to TimeZero) button of the Polaires step
writes **two XML files in one go**:

- `..._Wind_Polar.xml` — the speed polar;
- `..._Sail_Set.xml` — the **sail plan to carry**, by range of angle and wind.

The second one is the rarest thing Allure has to offer: TimeZero can
display which canvas to carry depending on angle and wind, and that is
exactly what the max polar computes — the winning configuration of each
cell. Routing therefore receives not only what the boat can do, but **what
it does it with**, deduced from what it has actually done at sea.

TimeZero does not publish the specification of its files; the format was
worked out from two real files from the boat. Three conventions in it are
silent traps, and the export respects them: the decimal separator is a
**comma** (`10,5` — a point produces a file that TimeZero reads without
complaint but interprets wrongly); a cell without data is **`0,1`**,
neither 0 nor empty; and all the curves carry exactly the same list of
angles. The sail colors are those of TimeZero, so that the boat does not
change appearance from one file to the other.

One thing has to be set once: TimeZero does not know your sail codes. In
the gear icon → *Voiles & moteurs*, **Export TimeZero** section, say what
each of yours is — mainsail (`GV`, 1 to 3 reefs, or `Off` when lowered) or
headsail classified by wind strength and point of sail. A sail left without
a match is simply absent from the sail plan file; the speed polar, for its
part, does not depend on it. In TimeZero: **TIMEZERO → Ouvrir un fichier
polaire → Parcourir** button (TIMEZERO → open a polar file → browse).

---

## 8. Sources and acquisition

### Choosing your sources

A gateway often carries the same measurement twice — two wind vanes, for
example — which do not say the same thing. In *Sources & tampon*, first
give each channel a name (Station nav, Météo France, GPS…), then click
**Analyser les voies** (analyze the channels): the application reads the
rolling buffer again and lists what each channel actually carries, with its
rate, its unit and a sample value. You then designate, **for each quantity
separately**, the channel that is authoritative and the one that takes over
if it falls silent. A gateway may very well carry the best wind vane and
the worst log: that is why the order is specific to each measurement, not
to the channel.

The table can be **sorted by clicking the headers** (grouping by sentence
brings together the channels that carry the same wind — that is how you
compare them) and it is **actionable**: a double-click on a usable row
designates that channel as the source of the quantity, the former one
automatically becoming the fallback. The *Source* column shows which one is
authoritative. Seeing what a channel carries and choosing it are thus the
same gesture. What you designate is remembered **per channel**, not by the
name you gave it: renaming a device does not make the choice disappear. The
choice of **speed over ground** is also truly decisive: with two GPS units
on board, it really is the designated channel that feeds the measurement,
and not the last one to arrive.

### What a channel "apporte" (provides)

It reads in two parts: first the sentence itself, then what it feeds —
`[polaire : vent apparent]` (polar: apparent wind), `[fiche : pression, humidité…]`
(sheet: pressure, humidity…), or both. A sentence that goes into no polar
is not lost for all that: the temperature from the XDR sentences or the
station's pressure fill in the detail sheet of an archived minute, and the
table says so, instead of announcing "non exploitée" (not used). If you try
to designate one as a source, the refusal reminds you where it *is* used.

### The onboard weather station (`$PEUMA`)

**Proprietary** sentences (those starting with `$P`) carry the
manufacturer's name and not a standardized code; they are displayed under
their full name, and the one from this boat's weather station is fully
decoded:

- **pressure** at station level **and** reduced to sea level — on this
  installation, it is the **only** source of pressure on board (no MDA/MMB
  sentence circulates there);
- **air temperature** and **humidity** (they match the XDR values exactly);
- **course and speed over ground** — the speed is sent in meters per second
  and converted automatically; the course agrees to within 0.4° with that
  of the VTG sentences. The station therefore **competes with the GPS
  units** for the *Vitesse fond* (speed over ground) quantity: it can be
  designated as a source in the table, exactly like a VTG, and it also
  feeds maneuver detection and the automatic theme (through its position).
  This is risk-free: a truncated sentence never makes it a source (see
  below), and speed over ground builds **no** polar — it is used for the
  display, for the STW/SOG cross-check and for maneuver detection. The wind
  and the speed through water, on the other hand, remain reserved for
  standardized sentences: they are what the polar is built on;
- **measured true wind**: mean, gust, minimum, and its direction. This
  wind is **referenced to north**, not to the bow: it can therefore never
  serve as a TWA (that would require an instantaneous compass heading,
  which the installation does not provide). On the other hand, it
  **arbitrates the wind strength** — it is what made it possible to expose
  this boat's badly calibrated wind vane. For that reason it appears in
  *Comparaison des girouettes* (wind vane comparison), in the live view,
  which is displayed **even with a single wind vane** as long as the
  station is present, and flags any wind vane that deviates by more than
  25% from its measurement.

All these quantities are offered in the detail sheet of an archived minute
(gear icon → *Lissage & table*). Truncated sentences — there are always
some in a real buffer — are discarded entirely: a proprietary sentence has
no field markers, so a truncation would silently shift all of its content
and a pressure would become a speed.

### A designated source that provides nothing cannot shut off the measurement

This is a real trap, and it is worth explaining: a channel that sends only
`MWV,ref='T'` (a wind direction relative to north, which the application
deliberately rejects) **talks** about the wind without ever **giving** any.
Naively designated as the wind source, it would win the arbitration on
every sentence; the real wind vane would be silently set aside, the dial
would stay empty and no more samples would be produced — without the
slightest message. Two rules prevent this: a channel only counts as the
source of a quantity if it **actually provides** that quantity, and a
designated source that has **never provided anything** is ignored (channel
unplugged, renamed, setting imported from another installation) rather
than shutting off the measurement. In both cases, the live view displays a
banner that **names the faulty channel and the one that actually
provides**, and says where to correct it. A setting that does not produce
what is expected of it must make itself known.

### The Cadence column

Watch out for the **Cadence** (rate) column: it gives the *useful* rate. A
channel that sends wind twice per second but includes apparent wind in
only one out of two is twice as slow as it looks — and it is that rate that
determines how many samples you will get.

### Automatic stop on a maneuver

Paramètres → *Manœuvres*, disabled by default. Two distinct things share
this page: the **sample filter** (TWA/STW over the smoothing window — it
discards a few measurements, silently) and the **automatic stop** — this
one detects that you have **tacked or changed your point of sail**, from the
**course over ground (COG)** and the **speed over ground (SOG)** of the VTG
sentences (or of the `$PEUMA` station, which competes with them), and
therefore without depending on the wind vane and anemometer. When its
thresholds are crossed (a course sweeping more than X°, or a speed varying
by more than Y% over the observation window), Allure **beeps**, displays a
**red banner visible on every page** and opens a dialog box:
*"Avez-vous manœuvré ?"* (did you maneuver?). **Oui** (yes): the recording
stops, and the measurements taken **since the moment of detection** are
discarded (the maneuver and the time taken to answer do not belong in the
polar) — the session goes into the store clean. **Non** (no): false alarm,
the recording continues, and detection resumes after a debounce period.
Closing the dialog box with the cross is not an answer: the banner stays,
and you can answer later. Banner and dialog box ask the same question — the
first one answered wins. Safeguards: nothing triggers as long as the
observation window is not filled enough (the first seconds of a recording
are not a maneuver), the course is ignored below 1.5 kn of SOG (the COG of
a stationary GPS is noise), and the course difference is computed
**circularly** (359° and 1° differ by 2°, not by 358). These thresholds can
be changed on the fly, even in the middle of a recording.

### Damping of live values

Paramètres → *Affichage & fenêtre*: the large digits and the arrows of the
live view are averaged over this duration (5 s by default, 0 = raw
values). Without it, they jump with every sentence and become unreadable at
sea. This affects **only the display**: neither the recorded samples, nor
maneuver detection, nor the polar depend on it — the smoothing of the
*measurement* is set in *Lissage & table*. Angles are averaged circularly:
dead downwind, an ordinary average would give head to wind.

---

## 9. Troubleshooting

**"No module named tkinter"** — Python was installed without tcl/tk. Run
the installer again, choose *Modify*, and check `tcl/tk and IDLE`.

**"No module named matplotlib"** — the installation command was not run,
or was run for a different Python. The simplest fix: double-click
`installer_dependances.bat`, which installs for the `python` command and
displays the path of the interpreter concerned. By hand:
`python -m pip install matplotlib` — or, if your shortcut launches a
different Python, `"C:\Chemin\vers\python.exe" -m pip install matplotlib`:
the `-m pip` form guarantees that you install for THAT Python. No internet,
or `WinError 10013` in the message: see *Offline installation*
(section 1).

**No sentences come in, in live mode** — check in *Paramètres*
(*Sources & tampon*) that the active UDP channels have the right port
numbers (by default, two channels are active, on ports 4001 and 4003; two
others, 10112 and 10113, are ready but disabled), and that the Windows
firewall allows Python to receive on the local network (see section 3,
*The firewall and `pythonw.exe`*).

**Startup is slow (a command prompt, then nothing for many seconds, or even
minutes).** Startup is timed and **reported**: the console displays each
phase, and `journaux/demarrage.log` keeps the details of the last launch
(also summarized in ⚙ → *Sauvegardes* → *Diagnostic de l'installation*).
Read it: if most of the time goes into "bibliothèques" (libraries), the
culprit comes **before** Allure's first line — either the first import of
matplotlib after an installation/update (its font cache is rebuilt, once),
or the **antivirus** inspecting the hundreds of matplotlib/numpy files at
each launch: add the Python folder to the antivirus exclusions, and startup
becomes short again.
Allure's own phases (data, interface, network) take seconds, even with a
well-stocked store.

**A clock change on board (time zone, winter time, clock reset).**
This is a normal event on a boat that crosses time zones, and Allure
absorbs it without losing anything. Here is exactly what happens when the
clock **goes back**:

- a recording in progress **continues**: the jump is detected, the
  smoothing windows are restarted (cost: the ~15 seconds it takes to fill
  them), the active configuration follows the new time, and the
  *Réception* section displays "Changement d'heure détecté (1 fois) :
  fenêtres de mesure réamorcées, rien n'est faussé" (clock change detected,
  once: measurement windows restarted, nothing is distorted);
- **replaying a log** (importing a `.log` file, recomputing from the
  buffer) can tell a step back of one or two hours (a clock change: the
  times are kept as written) from a passage through midnight (a step back
  of almost 24 h: a day is added) — confusing the two would send everything
  after the change 23 h into the future, outside any annotation, which
  means it would be lost;
- the **rolling buffer** never purges beyond what the data tell: even a
  clock wrongly set ten days ahead cannot make it delete the last 48 h of
  recorded stream.

A clock going **forward**, on the other hand, appears as a simple gap in
the stream and requires nothing special. The only real ambiguity left,
inherent to the problem: during the hour in the fall that exists **twice**
(02:00 → 03:00 → clocks go back → 02:00), the archives of those two hours
carry the same timestamps and read as mixed together — no format based on
local time can do better.

**Close-hauled angles that seem too tight** — it is almost always the wind
vane, not the computation. When two channels report apparent wind, the
live view displays the *Comparaison des girouettes* block: each channel
shows the TWA it **would give** if it were the source. A few degrees of
difference in the apparent angle, or 15% in the strength, translate into
about ten degrees of difference in the TWA — and only the selected source
(marked ▸) builds your polar. To decide, compare with the true wind that
your gateway computes itself, then designate the right channel in
*Sources & tampon*.

**Repairing runs already recorded on the wrong channel.** Changing the
source only affects future recordings: the samples already stored keep the
angle computed at the time of recording. But as long as a run is still
covered by the rolling buffer, its raw sentences are still there: select it
in the Entrepôt and click **Recalculer depuis le tampon** (recompute from
the buffer). Everything is replayed with the current settings — nothing is
estimated, nothing is extrapolated. The annotation (sails/engines) is kept,
the report puts figures on what has changed, and the old version goes to
the Corbeille, from which it comes back with one click. Beyond the retained
window, the application refuses rather than produce a truncated result
— hence the value of a generous retention if you sail several days in a
row.

**No samples produced after a session** — the application displays a
precise diagnosis that distinguishes the three possible causes: nothing
received, received but no configuration checked, or received but never
stable enough. The session's raw file is always kept in that case: you can
reimport it after correcting the setting.

---

## 10. License

Allure is **free software**, distributed under the
**GNU General Public License, version 3 or later** (GPL-3.0-or-later).

Copyright © 2026 ETDEL.

In practical terms, you may use the program freely, including on board and
in a professional setting, study it, modify it and redistribute it —
provided that any redistributed version, modified or not, remains under the
same license, with its source code and the copyright notice. The program
is provided **without any warranty**: the polars it produces help you tune
the boat, they replace neither your judgment nor your navigation documents.

The full text of the license is in the repository's `LICENSE` file and at
<https://www.gnu.org/licenses/gpl-3.0.html>. Each source module
(`allure.py`, `allure_engine.py`, `allure_config.py`, `allure_buffer.py`)
carries the corresponding license header, and the two `.bat` scripts repeat
the short notice.

---

*Allure — ETDEL 2026 — GPL-3.0-or-later*
