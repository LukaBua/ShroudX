# Core Build Prompt Pack: SPHEREx Sky-Motion Viewer

NASA Space Apps 2026, challenge "Planet X and SPHEREx". Core scope only.

## Start here: find your part

Find your name in the table, then jump to those headings with the document outline or by searching for the prompt number. Every prompt heading below now ends with its owner's name.

| Person | Your core prompts (in order) | Your Phase 2 prompts (hour 12 onward) | Also read |
| --- | --- | --- | --- |
| Luka (Lead) | Prompt 0 (everyone uses it), Prompt 1, Prompt 3, Prompt 8 | Reviews, README results tables, Release checklist | Luka: your timeline from t = 0, Team split, Who waits for whom, Timeline, Working rules, Checks you must do yourself |
| Aghamusa (Statistician) | Prompt 5 | Prompt 9 (flicker experiment), then Prompt 12 (blind mover search) | Aghamusa: your timeline from t = 0, How to start your AI session, Phase 2 context |
| Emin (Aerospace Engineer) | Prompt 2 | Prompt 10 (motion and parallax fit) | Emin: your timeline from t = 0, How to start your AI session, Checks you must do yourself, Phase 2 context |
| Shayan (Frontend, Backend) | Prompt 7 (frontend) first, then Prompt 6 (app.py) | Prompt 11 (motion endpoint and panel), then Prompt 13 (mover endpoint and overlay) | How to start your AI session, `docs/api_contract.md` (from Luka), Phase 2 context, Shayan: your timeline from t = 0 |
| Umair (Mechanical Engineer) | Prompt 4 | Prompt 14 (hosted deployment), plus fetching the Phase 2 fields | Umair: your timeline from t = 0, How to start your AI session |

**Everyone reads these four things first (about 10 minutes):**

1. How to use this pack (next section).
2. Team split and Who waits for whom.
3. How to start your AI session.
4. Your own row in the table above.

**How this document is ordered:** plan and team (Team split, Who waits for whom, Timeline, Shared interfaces, Working rules) → setup (How to start your AI session, then one timeline section per person) → core prompts 0 to 8 → Phase 2 plan and context → Phase 2 prompts 9 to 14 → Release checklist.

## Before the event: checklist (now to November 13)

The event starts November 14. Between now and the evening of November 13, the goals are: get the rules answered in writing, make every laptop ready, and write no project code that the rules might not allow.

**Part 1: Luka sends the rules questions today**

Send this message to your Local Lead or Local Event organizers, and keep their written answer. Be upfront that a prototype exists; do not leave it out.

```text
Hello, we are a team of five registered for [Local Event name], working on the challenge 'Planet X and SPHEREx'. Before the event we want to confirm three rules so we follow them exactly.
1. Pre-existing work: our team has an earlier prototype of a tool for this challenge, built before the event. Is any pre-existing code allowed, and if so how should we disclose it? If not, we will write all code from scratch after the hackathon starts.
2. AI tools: are AI coding assistants allowed? Must we disclose their use in the submission?
3. Preparation: may we create an empty repository (folders and configuration files only) and share planning documents within the team before the event starts?
Please also tell us the submission deadline in local time and the required video length and format. Thank you.
Team: [team name]. Contact: [name, email].
```

**Part 2: what the answer means**

| If the answer is | Then |
| --- | --- |
| Pre-existing code is allowed with disclosure | Disclose it in the README and in the submission. Spend the event on the Phase 2 features, which are the real contribution. |
| Pre-existing code is not allowed | Start the repo at t = 0 and rebuild everything from the prompts. Do not push or reuse the earlier code. Prompt 1 takes only minutes. |
| The scaffold and an empty repo are allowed in advance | Create the repo before the event and invite everyone. |
| The scaffold is not allowed in advance | Create the repo at t = 0. |
| AI coding assistants are allowed with disclosure | Keep a short list of which modules were AI-assisted and put it in the README. |
| AI coding assistants are not allowed | Stop using this pack's prompts and tell Luka immediately; the plan changes. |
| No answer by the evening of November 13 | Read the Judging and Awards Guide and the Project Submission Guide (published November 13) for the rules, and follow the strictest reading. |

**Part 3: Luka reads the guides on November 13**

1. Read the Judging and Awards Guide and the Project Submission Guide the day they appear.
2. Copy the rules on pre-existing work, AI use, open source, video length and format, deadlines and required fields into the team chat.
3. Tell the team which row of the table above applies.

**Part 4: every laptop is ready**

Install one set of libraries everywhere. In a terminal:

```text
pip install numpy scipy astropy reproject astroquery pyvo fastapi uvicorn httpx pillow pytest ruff matplotlib
```

On Windows, a virtual environment avoids version clashes: `py -3.12 -m venv .venv`, then `.venv\Scripts\activate`, then the command above. `httpx` is needed for FastAPI's test client. Test git on any throwaway repository, not on the project.

| Person | Also needed | Ready check (run it, see the result) |
| --- | --- | --- |
| Everyone | Python 3.10 or newer, git, an editor, your AI tool with a long prompt pasted in | `python --version` prints 3.10 or newer; `python -c "import numpy, scipy, astropy, reproject, fastapi, pyvo"` prints nothing and no error; you can clone, branch, push and pull on a throwaway repo |
| Luka | The rules answer in writing; `docs/api_contract.md` ready | `pytest` passes in the project; the contract file exists |
| Aghamusa | `matplotlib` for Phase 2 | `python -c "import reproject; print(reproject.__version__)"` prints a version |
| Emin | A list of where positions, proper motions and parallaxes will come from (SIMBAD, Gaia, papers) for the five demo targets | `python -c "from astropy.coordinates import SkyCoord; print(SkyCoord.from_name('Barnard star'))"` prints a position |
| Shayan | A browser on a laptop and on a phone | A FastAPI hello-world app starts with `uvicorn`, and a plain HTML file opens on your phone |
| Umair | The cutout script from his timeline section, kept outside the project | The script prints how many images it found and shows the FLAGS header |

**Part 5: team readiness**

1. Everyone reads: Start here, Team split, Who waits for whom, their own timeline section, and their own prompts.
2. Agree on one team chat channel and who answers there first.
3. Agree on sleep: over 48 hours, plan who sleeps when, so someone is always awake and the Lead is reachable.
4. Check the venue: on the day, confirm that the venue network can reach IRSA (irsa.ipac.caltech.edu) before you rely on it, and bring chargers.

**Part 6: what not to do before the event**

1. Do not write project code beyond what the organizers approve in writing.
2. Do not download demo fields into the project or build the cache; the cutout script is a throwaway check kept outside the repo.
3. Do not push the project publicly or share it outside the team.
4. Do not ask the AI to build modules early.
5. Do not rely on a verbal answer; get it in writing.

## How to use this pack

1. Start a fresh AI coding session. Paste Prompt 0 first, then run your own prompts (see 'Start here: find your part'), one at a time. Run the tests and commit after every prompt. Do not start the next prompt until the previous module's tests pass. Frequent commits also give you a timestamped record that the work was built during the event.
2. Build from scratch. Do not paste any source file from the earlier project into the AI. This pack contains only documented facts about the data format and lessons about pitfalls, not code.
3. Read the Judging and Awards Guide (published November 13) for the rules on AI-assisted development and on disclosing pre-event preparation, and follow them. I have not seen the 2026 text, so confirm this yourself.
4. Verify data facts against IRSA's SPHEREx tutorials (spherex-intro and spherex-cutouts) and the SPHEREx Explanatory Supplement. If the AI contradicts the documentation, the documentation wins. In particular, take the FLAGS bit definitions from the MP\_\* keywords in a real FLAGS header, not from memory.
5. Replace anything in \[square brackets\] before sending.

## What counts as core

In scope: coordinate and name input, IRSA ingestion with caching, flag decoding and masking, zodiacal-light and background subtraction, grouping by detector and wavelength window, epoch splitting, alignment to a common sky grid, inverse-variance stacking, a FastAPI backend, and a single-file blink viewer with shareable URLs.

Out of scope until the core works end to end on real data: difference maps, proper-motion fitting, spectra, catalog overlays, pixel inspector, validation scripts, a blind mover search, and the colour-flicker experiment.

Suggested target: a working core on real WISE 0855 data within the first 12 to 14 hours, leaving the rest of the weekend for the science features and the submission.

## Team split

Each person owns one or two modules and works in their own AI session and git branch. Luka carries the coordination work; Emin and Umair finish early and move to verification.

| Person | Role | Prompts | Modules and tasks |
| --- | --- | --- | --- |
| Luka | Lead | 0, 1, 3, 8 | Repo scaffold, `frame.py`, `flags.py`, the API contract (`docs/api_contract.md`), merges, integration, README, final self-review |
| Aghamusa | Statistician | 5 | `processing.py`: background and zodi subtraction, wavelength and epoch grouping, alignment, inverse-variance stacking with sigma clipping, variance handling. Later (Phase 2): the colour-flicker experiment, then the blind mover search, and review of the motion-fit error model |
| Emin | Aerospace Engineer | 2 | `coords.py`: coordinate parsing, name resolution, proper-motion propagation. Fills in literature positions and proper motions for the demo targets. After that: the real-data checks listed in this doc, then leads the motion and parallax fit (Phase 2) |
| Shayan | Frontend and Backend | 6, 7 | `static/index.html` first (against mock JSON), then `app.py` once the backend modules land |
| Umair | Mechanical Engineer | 4 | `data.py` and `scripts/fetch_cutouts.py`: IRSA query, cutout download, cache, frame construction. Then pre-fetching every demo field |

## Who waits for whom

Only Luka's `frame.py` blocks the first hour; after that, four people work in parallel and the real dependencies are few.

| Person | Can start | Blocked by | What they unblock |
| --- | --- | --- | --- |
| Luka | Immediately | Nothing for Prompts 0 and 1; `flags.py` needs a real FLAGS header | Everyone: `frame.py` and the API contract |
| Emin | Immediately (`coords.py` has no dependencies) | Nothing | Shayan's `app.py` |
| Aghamusa | When `frame.py` is pushed (hour 1) | `frame.py` | Shayan's `app.py` |
| Umair | When `frame.py` is pushed (hour 1); can code against a stub until `flags.py` lands | `frame.py`, then `flags.py` | Shayan's `app.py`; everyone's real-data testing |
| Shayan, frontend | When the API contract is pushed (hour 1) | The contract only; uses mock JSON | Final integration |
| Shayan, `app.py` | When coords, data and processing are merged (about hour 8) | Emin, Umair, Aghamusa | Luka's Prompt 8 |
| Luka, Prompt 8 | When all modules are merged | Everyone | Release candidate |

If a module is late, its consumer builds against a stub or synthetic frames and swaps in the real module when it lands. Nobody waits idle.

## Timeline

Hours count from the start of the event. Targets are suggestions, not promises.

| Hours | Who | What | Gate |
| --- | --- | --- | --- |
| 0 to 1 | Luka | Prompts 0 and 1; push `frame.py`; write `docs/api_contract.md` | Gate 1: `frame.py` and the contract are in `main` |
| 0 to 1 | Everyone else | Set up environments, read the IRSA docs and this pack. Emin starts `coords.py`. Umair downloads one real WISE 0855 cutout by hand and shares it |  |
| 1 to 3 | Luka | Print the FLAGS header's MP\_\* keywords from Umair's cutout, then run Prompt 3 | Gate 2: `flags.py` merged (about hour 3) |
| 1 to 8 | Emin, Aghamusa, Umair, Shayan | Each runs their own prompt in parallel: `coords.py`, `processing.py`, `data.py`, `index.html` against mock JSON | Each owner merges to `main` only when their tests pass |
| 3 to 4 | Umair | Swaps the stub for the real `flags.py`; starts pre-downloading demo fields on the venue network as soon as `data.py` works |  |
| 8 to 12 | Shayan | Writes `app.py` against the merged modules; points the frontend at the real API | Gate 3: `/api/meta` and `/api/image.png` return real frames |
| 8 to 12 | Luka | Prompt 8: integration test, README, self-review |  |
| 8 to 12 | Emin, Aghamusa | Real-data checks (below); report bugs to the owner |  |
| 12 to 14 | Everyone | Fix bugs found in the checks; deploy a hosted copy | Gate 4: core works on real WISE 0855 data end to end |
| 14 onward | Everyone | Science features and submission (see the last section) |  |

## Shared interfaces

Agree on these at hour 0 and change them only through Luka, who announces any change to everyone.

1. `coords`: `parse_position(ra, dec)` returns decimal degrees; `resolve_name(name)` returns a `ResolvedTarget`.
2. `flags`: `flags_to_mask(flags, names)` returns a bool array.
3. `data`: `get_frames(ra, dec, fov)` returns a tuple of `Frame` objects sorted by MJD.
4. `processing`: `clean_frame`, `group_frames`, `split_epochs`, `align_frames`, `stack_frames`, `render_png`.
5. `app`: `/api/resolve`, `/api/meta`, `/api/image.png`, `/health`.

## Working rules for the team

1. Everyone starts their AI session with the three steps in 'How to start your AI session' below.
2. One branch per person; merge to `main` only when tests pass; commit often.
3. Do not edit another person's module. Raise it with its owner, or with Luka if the owner is unavailable.
4. If a test in someone else's module fails, report it with the failing input; do not patch around it.
5. Never merge a change that makes the app silently show synthetic data.

## How to start your AI session

Your AI assistant knows nothing about our project until you show it. If you skip this, it will guess names and formats, and your module will not fit with the others when we merge. Do these three steps at the start of every session, in this order.

1. **Send Prompt 0.** Copy it from the Prompt 0 section below and send it as your first message. It explains the project and the rules.
2. **Send the shared files.** These are `frame.py` and the interface list from the Shared interfaces section above. The exact message is below.
3. **Send your own prompt**, the one assigned to you in the Team split table.

**What is frame.py?** It is a small file that defines what one SPHEREx image looks like inside our code. Think of it as a standard shipping box. `data.py` puts an image into the box, `processing.py` takes it out and cleans it, and `app.py` sends it to the screen. Every module must use the same box, or they cannot pass images to each other. The box has labelled slots: `image`, `variance` (how noisy each pixel is), `mask` (which pixels are bad), `wavelength`, `detector`, `mjd` (the observation time), `wcs` (where each pixel is on the sky), and a few extras.

**Why does the AI need it?** Without the real file, the AI invents its own slot names, for example `wavelength_um` instead of `wavelength`. Your code would then look fine on its own and break the moment it meets someone else's module.

**How to give it to the AI**

- If you use a chat window (claude.ai, ChatGPT): open `frame.py` in the repo, select everything, copy it, and paste it into the message below where it says to.
- If you use a coding tool that can see the repo (Claude Code, Cursor): do not paste. Send this one line instead: Read frame.py in this repo. Use the Frame class exactly as defined, and do not rename or change its fields.

**Message to send as your second message (chat window version):**

```text
This is our team's shared data class. Use it exactly as written. Do not rename fields or change types.

--- frame.py ---
[PASTE THE WHOLE FILE HERE]

--- Module interfaces (the functions other people's modules provide) ---
[PASTE THE INTERFACE LIST HERE]

Reply with one line confirming you will use these exactly, then wait for my task.
```

The interface list is the five numbered lines in the Shared interfaces section above. Copy those lines and paste them where the message says.

**Who needs what**

| Person | Paste frame.py? | Also give the AI | Why |
| --- | --- | --- | --- |
| Luka | No for Prompt 1 (you are creating it) | The interface list | You write `frame.py` and `flags.py`, which work on plain arrays |
| Emin | No for `coords.py`; yes in Phase 2 for Prompt 10 | The interface list | `coords.py` never touches a Frame; the motion fit does |
| Aghamusa | Yes | The interface list | `processing.py` reads and returns Frames |
| Umair | Yes, and `flags.py` once it is merged | The interface list | `data.py` builds Frames and uses the flag decoder |
| Shayan, frontend | No | `docs/api_contract.md` | The page only talks to the API, never to Frames |
| Shayan, `app.py` | Yes | The interface list and the real files of the modules it calls | `app.py` connects all the other modules |

**How to check it worked.** Ask the AI: What fields does Frame have? It should list the real ones from the file (`image`, `variance`, `mask`, `wavelength`, `detector`, `mjd`, `wcs` and so on). If it lists different names, send the file again.

**If frame.py is not ready yet.** Luka pushes it in the first hour. Run `git pull`, then copy it. Until then, read the IRSA docs and this pack, and set up your environment.

## Umair: your timeline from t = 0

You own the data layer (Prompt 4). Everyone's real-data testing depends on you, so your first job is to get one real cutout and the FLAGS header to Luka within the first hour. Times count from the start of the event.

| When | What to do | Done when |
| --- | --- | --- |
| Before the event (optional) | Install Python 3.10+ and `pip install pyvo astropy numpy scipy reproject astroquery pytest`. Run the cutout script below once to confirm your network and install work. Keep that script outside the repo. | The script prints how many images it found and writes `first_cutout.fits` |
| t = 0 to 0:15 | Clone the repo, create your branch, run the cutout script again if you did not do it earlier. | You have `first_cutout.fits` and the printed FLAGS header |
| By t = 1:00 | Send Luka the whole printed FLAGS header (the lines starting with `MP_` matter most). Luka needs it for `flags.py` (Prompt 3). | Luka confirms receipt |
| t = 0:15 to 1:00 | While `frame.py` is not pushed yet: read the IRSA cutouts tutorial, decide how you will cache files (folder per position, an index file), and open your AI session with Prompt 0. Do not paste `frame.py` yet. | `frame.py` appears in `main` |
| t = 1:00 | Run `git pull`. Send the shared-files message with the real `frame.py` and the interface list (see 'How to start your AI session'). Then send Prompt 4. | The AI confirms it will use `Frame` exactly |
| t = 1 to 4 | Build `data.py` with Prompt 4. `flags.py` is not ready until about t = 3, so ask the AI to use a temporary stand-in with the same function name, in a separate file you will delete later. | Tests pass; one real cutout loads as a `Frame` |
| t = 3 to 4 | Swap the stand-in for the real `flags.py` as soon as Luka merges it. Delete the stand-in. | Tests still pass with the real flag decoder |
| t = 4 to 5 | As soon as `data.py` works, start downloading demo fields on the venue network (see 'What to fetch first'). This is slow, so start early and let it run while you finish tests. | Fields are cached and listed in a note for the team |
| By t = 8 | Merge `data.py` to `main` once its tests pass. Shayan needs it for `app.py`. | Gate 3 can be tested |
| t = 8 to 12 | Help with the real-data checks: if any position fails to load, find out why and fix it. | The team can load WISE 0855 and one other target |
| t = 12 to 16 | Phase 2 fields: three or more fields with bright stationary stars, the benchmark fields, and three control fields with no known fast movers. Emin and Luka send you the positions. | Fields cached and listed in the README |
| t = 22 to 32 | Prompt 14 (hosted deployment), then control-field runs for Aghamusa. | The hosted copy answers /health |
| t = 36 to 40 | Final hosted deployment; bug fixes only. | Feature freeze at t = 40 |

**The cutout script (throwaway exploration, based on IRSA's own tutorial)**

```python
import pyvo
from astropy.io import fits

ra, dec, size = 210.80227, 54.34895, 0.05   # degrees; M101, the position used in IRSA's tutorial

service = pyvo.dal.TAPService('https://irsa.ipac.caltech.edu/TAP')
query = f'''
SELECT 'https://irsa.ipac.caltech.edu/' || a.uri || '?center={ra},{dec}d&size={size}' AS url,
       p.time_bounds_lower AS mjd, p.energy_bandpassname AS band
FROM spherex.artifact a
JOIN spherex.plane p ON a.planeid = p.planeid
WHERE 1 = CONTAINS(POINT('ICRS', {ra}, {dec}), p.poly)
ORDER BY p.time_bounds_lower
'''
rows = service.search(query).to_table()
print(len(rows), 'images found')

with fits.open(rows['url'][0]) as hdul:
    hdul.info()                                  # lists the HDUs
    hdul.writeto('first_cutout.fits', overwrite=True)
    print(repr(hdul['FLAGS'].header))            # the text Luka needs
```

Expected: a few dozen images found; `hdul.info()` lists IMAGE, FLAGS, VARIANCE, ZODI, PSF and WCS-WAVE; the file is a few MB. IRSA sometimes drops connections: run it again before assuming anything is wrong. If you prefer a web page, IRSA's SPHEREx Data Explorer can generate a download script with cutouts, but you still need Python to read the FLAGS header.

**What to fetch first**

1. Use `coords.py` from Emin for the positions of moving targets. Do not type the challenge's published WISE 0855 coordinates directly: that position is about 2 arcminutes from where the object is during SPHEREx observations, so the field would look empty.
2. Use a cutout size of 480 arcsec for the main demo targets. A smaller field can be cropped from it later, but not the other way round.
3. Download at most 5 files at once, and keep a list of what is cached and where.
4. If a position returns no images, tell Luka; that is a result, not a failure of your code.

**If you get stuck**

- IRSA is slow or down: say so in the team chat and work on tests with the cutout you already have.
- The AI invents a field name or a flag meaning: stop, paste the real `frame.py` or the real FLAGS header again, and ask it to correct itself.
- Someone else's module breaks yours: send the failing input to its owner; do not patch around it.

## Luka: your timeline from t = 0

You are the Lead. You write the foundation everyone else builds on, then keep the team unblocked and the claims honest. Your first deliverables are `frame.py` and `docs/api_contract.md`, both pushed within the first hour. Times count from the start of the event.

| When | What to do | Done when |
| --- | --- | --- |
| Before the event | Read the Judging and Awards Guide and the Project Submission Guide when they are published (November 13). Message the Local Lead about pre-existing work, AI-assisted coding and pre-event repo setup, and keep the answer in writing. Make sure everyone has Python, the libraries and working git. | Written answers; every laptop can clone, branch, push and pull |
| t = 0 | Create the repo (if the rules allow it earlier, do this before). Send Prompt 0, then run Prompt 1 (scaffold and `frame.py`). Push to `main` and tell the team. | `frame.py` and the scaffold are in `main` |
| t = 0:15 to 1:00 | Write `docs/api_contract.md`: the endpoints and JSON shapes from Prompt 6 (`/api/resolve`, `/api/meta`, `/api/image.png`, `/health`). Push it. Shayan starts the frontend from it. | Gate 1: `frame.py` and the contract are in `main` |
| By t = 1:00 | Get the real FLAGS header from Umair. Run Prompt 3, pasting the bit table from that header into the prompt. | Header received |
| t = 1 to 3 | Finish `flags.py` and merge it. Umair is waiting for it to replace his stand-in. | Gate 2: `flags.py` merged (about t = 3) |
| t = 3 to 8 | Review teammates' merges: tests pass, interfaces match the Shared interfaces list, no silent synthetic data. Answer blockers. Announce any interface change to everyone. | Coords, data and processing are merged by t = 8 |
| t = 8 to 12 | Run Prompt 8: integration test, README, self-review. Run the 'Checks you must do yourself' list on real data. | Integration test passes |
| t = 12 to 14 | Decide whether Gate 4 is passed: the core works end to end on real WISE 0855 data. If not, fix the core before any Phase 2 work. | Gate 4 |
| t = 12 to 16 | With Emin, fill `data/benchmarks.csv` and `data/flicker_stars.csv` with a citation per row. Send Umair the positions he needs. | Literature values recorded |
| t = 14 to 36 | Review Phase 2 work against its pass criteria. Update `docs/api_contract.md` for each new endpoint. Make sure results come from scripts, not hand edits. | Gates 5 to 7 reviewed |
| By t = 36 | Prepare the submission: required fields, video length and format, AI-use disclosure, what must be public. Write the README results tables from script output, the limitations section, and a section 'Built during the hackathon'. | Everything ready to paste |
| t = 36 to 40 | Final integration; bug fixes only; feature freeze at t = 40. | Gate 8 |
| t = 40 to 48 | Demo video with Shayan, write-up, one rehearsal, then submit with a margin of several hours. The Submit for Judging button is final. | Submitted |

**Lead duties throughout:** keep a running list of which gate each person is on; announce interface changes to everyone at once; do not merge anything that makes the app silently show synthetic data; make sure every number in the README is generated by a script.

**If you get stuck**

- A teammate is blocked: ask what they need, and give them a stand-in or synthetic frames to work against. Nobody waits idle.
- A module is late: do not merge it half-finished. Check its tests, and move its consumers to a stub.
- The core is not working on real data by t = 14: stop Phase 2 planning and fix the core first.

## Aghamusa: your timeline from t = 0

You own the statistics: `processing.py` first (Prompt 5), then the colour-flicker experiment (Prompt 9) and the blind mover search (Prompt 12). Your job is also to keep the team honest about uncertainty. Times count from the start of the event.

| When | What to do | Done when |
| --- | --- | --- |
| Before the event | Install Python 3.10+ and the libraries (`numpy`, `scipy`, `astropy`, `reproject`, `pytest`). Read Prompt 5 and Phase 2 Prompts 9 and 12. Read IRSA's SPHEREx documentation on Level-2 images and flags. | Environment works; you have read your prompts |
| t = 0 to 1:00 | While `frame.py` is not pushed: open your AI session with Prompt 0. Do not paste `frame.py` yet. Write down, in plain words, your plan for the window choice, the clipping and the variance handling, so you can check the AI's code against it. | `frame.py` appears in `main` |
| t = 1:00 | Run `git pull`. Send the shared-files message with the real `frame.py` and the interface list (see 'How to start your AI session'). Then send Prompt 5. | The AI confirms it will use `Frame` exactly |
| t = 1 to 8 | Build `processing.py`: `clean_frame`, `group_frames`, `split_epochs`, `align_frames`, `stack_frames`, `render_png`. Check the statistics yourself: noise in the stack should fall roughly as 1/sqrt(N); ask the AI to explain how it handles variance when reprojecting. Merge when tests pass. | Merged by t = 8 (Shayan's `app.py` needs it) |
| t = 8 to 12 | Run `processing.py` on Umair's real WISE 0855 cutouts. Check by eye: the wavelength window is narrow, the background is flat, and the source visibly shifts between epochs. Report bugs to the owner. | One real sequence looks right |
| t = 12 to 14 | Fix any processing bugs found by the integration checks. | Gate 4 |
| t = 14 to 22 | Prompt 9: the colour-flicker experiment. You need Umair's cached flicker fields and `data/flicker_stars.csv`. Report whatever the data show, even if it contradicts our assumptions. | Gate 5: flicker table and figures; `max_width` default set from data |
| t = 22 to 32 | Prompt 12: the blind mover search. Umair provides control fields. Fix the pass criteria in the script's docstring before running. | Gate 7: known movers recovered; false-candidate rate measured |
| t = 32 to 36 | Write the plain-language method and limitations for the README. Review Emin's error model for the motion fit. | Methods text ready |
| t = 36 to 40 | Bug fixes only; feature freeze at t = 40. | Gate 8 |
| t = 40 to 48 | Help Luka with the write-up and the video, especially the statistics and honest limitations. | Submitted |

**If you get stuck**

- The flicker fields are not cached yet: ask Umair, and test your script on synthetic stars in the meantime.
- The mover search is not working by t = 32: ship it as clearly labelled 'experimental' with its measured false-candidate rate. Do not hide the limitation.
- The AI produces a statistic you cannot justify: ask it to derive it, and do not use it until you can explain it in one sentence.

## Emin: your timeline from t = 0

You own `coords.py` (Prompt 2), then the real-data checks and the motion and parallax fit (Prompt 10). You also supply the literature numbers the AI must never invent. Your first module has no dependencies, so you start at t = 0. Times count from the start of the event.

| When | What to do | Done when |
| --- | --- | --- |
| Before the event | Install Python 3.10+ and the libraries (`astropy`, `numpy`, `pytest`). Read Prompt 2 and Phase 2 Prompt 10. Look up where positions, proper motions and parallaxes come from (SIMBAD, Gaia, papers) for Barnard's Star, WISE 0855, Epsilon Indi B and RW Aurigae. | Environment works; you know your sources |
| t = 0 to 1:00 | Open your AI session with Prompt 0, then send Prompt 2. You do not need `frame.py` for `coords.py`. | The AI starts writing `coords.py` |
| t = 1 to 4 | Fill the offline demo-target table yourself from SIMBAD, Gaia or papers: position, proper motion and reference epoch, with a citation for each. The AI must not supply these numbers. | Table filled, each row with a source |
| t = 4 to 8 | Test with the challenge's WISE 0855 coordinates in decimal and sexagesimal form, and check proper-motion propagation against a hand calculation. Merge when tests pass. | Merged by t = 8 (Shayan's `app.py` needs it) |
| t = 8 to 12 | Real-data checks with Aghamusa: enter the challenge's WISE 0855 coordinates directly, check that proper-motion handling puts the object in the field, and check that the blink shows motion between epochs. | The target is found and visibly moves |
| t = 12 to 16 | With Luka, fill `data/benchmarks.csv` and `data/flicker_stars.csv`, with a citation per row. Send Umair the positions he needs. | Literature values recorded |
| t = 14 to 24 | Prompt 10: `motion.py`, the parallax fit and `scripts/validate_motion.py`. Send the shared-files message with `frame.py` first. Fix the pass criteria before running. With fewer than 3 epochs the fit must report that parallax and motion are degenerate. | Gate 6: motion fit validated; results table generated by script |
| t = 24 to 34 | Review Shayan's motion panel with him. Review the error model with Aghamusa. If any benchmark disagrees with the literature, investigate the systematics and report them. | Panel and numbers agree |
| t = 34 to 40 | Put the validation table into the README from the script output only. Bug fixes only; feature freeze at t = 40. | Gate 8 |
| t = 40 to 48 | Help Luka with the write-up: explain the measurement and what it can and cannot show. | Submitted |

**If you get stuck**

- A literature value is unclear or sources disagree: record both with citations and tell Luka. Never let the AI pick one.
- A benchmark disagrees with the fit: do not tune parameters to match. Report it as DISAGREE and write down what you checked.
- Only two epochs are available: say so in the output and fit proper motion only.

## Shayan: your timeline from t = 0

You own the frontend (Prompt 7) first, then the API (Prompt 6), then the Phase 2 panels and endpoints (Prompts 11 and 13). The frontend can be built against mock data, so you can start as soon as Luka pushes the API contract. Times count from the start of the event.

| When | What to do | Done when |
| --- | --- | --- |
| Before the event | Install Python 3.10+, `fastapi`, `uvicorn` and `pytest`. Read Prompts 6, 7, 11 and 13. Make sure you can run a single HTML file in the browser on a laptop and a phone. | Environment works; you have read your prompts |
| t = 0 to 1:00 | Open your AI session with Prompt 0. Wait for `docs/api_contract.md` from Luka. Meanwhile sketch the page layout and prepare a few sample PNGs and a mock `/api/meta` JSON file. | The contract appears in `main` |
| t = 1:00 | Run `git pull`. For the frontend, give the AI `docs/api_contract.md` instead of `frame.py` (see 'How to start your AI session'), then send Prompt 7. | The AI confirms it will follow the contract exactly |
| t = 1 to 8 | Build `static/index.html` against your mock JSON: inputs, blink viewer, epoch timeline, compass and scale bar, status banner, shareable URL, loading, empty and error states. Test on a laptop and a phone. | Page works with mock data |
| By t = 8 | Pull the merged `coords.py`, `data.py` and `processing.py`. Send the shared-files message with `frame.py`, the interface list and the real modules, then send Prompt 6. | `app.py` starts |
| t = 8 to 12 | Finish `app.py` and point the frontend at the real API. Test every endpoint and every error code. | Gate 3: `/api/meta` and `/api/image.png` return real frames |
| t = 12 to 14 | Fix bugs. Test a shared URL on a second device. Enter the challenge's WISE 0855 coordinates directly. | Gate 4 |
| t = 14 to 24 | Prompt 11 UI against mock JSON: the Motion panel with centroids, arrow, fit table and verdict. Use the JSON shape written in Prompt 11. | Panel works with mock data |
| t = 24 to 36 | Prompt 11 against the real endpoint, then Prompt 13 (mover endpoint and overlay) when Aghamusa's `movers.py` lands. Update `docs/api_contract.md` with Luka. | Panels work with real data |
| By t = 36 | Add the real team bios to the team page. | Bios in |
| t = 36 to 40 | Bug fixes only; feature freeze at t = 40. | Gate 8 |
| t = 40 to 48 | Record the demo with Luka: the live URL, one real result, the validation table, one honest limitation. | Submitted |

**If you get stuck**

- A backend module is late: keep building against mock JSON and swap in the real endpoint when it lands.
- The UI would show synthetic data without saying so: stop. A prominent 'SYNTHETIC DATA' label is required.
- The page is too much work for the time you have: drop styling before you drop the status banner, the warnings or the shareable URL.

## Prompt 0: master context (everyone; paste first, and again at the start of any new session)

```text
You are my pair-programming partner for a 48-hour hackathon: NASA Space Apps 2026, challenge "Planet X and SPHEREx". We are building a public web tool where a user enters sky coordinates (RA/Dec) or an object name and sees a time series of SPHEREx images of that patch, as a blink ("like a little video") and as stacked images, so moving objects (brown dwarfs, nearby stars, asteroids, comets) are easy to spot.

Stack: Python 3.10+, numpy, scipy, astropy, reproject, astroquery/pyvo, FastAPI, uvicorn, pillow, pytest. Frontend: one static HTML/JS file, no build step. Flat repo layout: frame.py, coords.py, flags.py, data.py, processing.py, app.py, static/index.html, scripts/, tests/.

Working rules:
1. One module per request. Write the code, then pytest tests, then tell me how to run them. Do not touch other modules unless I ask.
2. Small, typed, documented functions. No dead code, no placeholder TODOs.
3. Never fabricate data, catalog entries, identifiers or benchmark numbers. If you do not know a fact about the SPHEREx file format, say so and ask me; I will check the IRSA documentation.
4. Never silently substitute synthetic data for real data. A synthetic mode may exist only when explicitly selected, and every API response from it must say "synthetic": true.
5. Do not claim performance numbers (speedups, latency) that you have not measured.
6. Fail loudly with clear user-facing messages, for example "no SPHEREx coverage at this position".

Facts about the data (from IRSA documentation). Treat these as requirements:
- SPHEREx Level-2 images are 2040x2040 pixel FITS files of hundreds of MB. Never download full images; request small cutouts through IRSA's cutout service.
- Each cutout has HDUs: PRIMARY, IMAGE, FLAGS, VARIANCE, ZODI, PSF, WCS-WAVE. Always read them by name, never by index. Drop PSF when caching (it is large).
- IMAGE and ZODI are in MJy/sr; VARIANCE is in (MJy/sr)^2; sigma = sqrt(VARIANCE). IMAGE is NOT zodiacal-light subtracted: subtract ZODI.
- FLAGS is a bitmask with 16 defined flags. Do not mask on flags != 0: the SOURCE bit (21) is set on many good pixels. Mask only genuine detector defects.
- Wavelength varies per pixel (Linear Variable Filters). It comes from the WCS-WAVE extension (spectral WCS, key "W", SIP off), evaluated at the target pixel. Each exposure sees the sky at a different wavelength, so a fixed star changes brightness between exposures without moving. Therefore frames may only be blinked or stacked together within the same detector (1 to 6) AND a narrow wavelength window (default 0.10 micrometres, configurable).
- Detector wavelength ranges (micrometres): 1: 0.75-1.09, 2: 1.10-1.62, 3: 1.63-2.41, 4: 2.42-3.82, 5: 3.83-4.41, 6: 4.42-5.00.
- Pixel scale is about 6.15 arcsec per pixel. Revisits of one patch cluster into "epochs" about six months apart: split epochs where consecutive observation times differ by more than 30 days (configurable).
- Frames come from different processing versions (for example l2b-v20). Record the version of every frame and never mix them silently.
- IRSA behaviour: TAP queries can take 4 to 17 seconds; parallel cutout downloads fail above roughly 5 workers; downloads drop occasionally, so retry with back-off and cache to disk.

Known pitfalls to design around:
- High proper-motion targets move: a catalogued position can be arcminutes away from where the object is during SPHEREx observations (WISE 0855 has moved about 2 arcminutes since its catalogued position). Name resolution must handle this explicitly.
- Every user-facing claim must be honest: show data version, wavelength window, frame count and warnings.

Acknowledge these rules in three lines, then wait for my first module request.
```

## Prompt 1: scaffold and frame.py (Luka)

```text
Create the repo scaffold and frame.py.

Scaffold: requirements.txt (minimum versions), pyproject.toml with pytest config (pythonpath ".") and ruff, .gitignore (.venv, __pycache__, .pytest_cache, cache/ downloads, frontend build output), tests/__init__.py, and empty scripts/ and static/ folders.

frame.py: a dataclass Frame with fields: image (ny, nx float array, MJy/sr, zodi NOT subtracted), variance, mask (bool, True = bad pixel), wavelength (micrometres at the target), detector (int 1 to 6), mjd (float), wcs (astropy WCS or None), zodi (optional array), version (int or None), uri (str or None), unreliable (optional bool array: saturated or non-linear pixels that stay usable for display and centroiding but not for flux), meta (dict or None).

Add: validate() raising ValueError on shape mismatch, detector outside 1 to 6, or non-finite wavelength or mjd; a property sigma = sqrt(variance) with non-positive variance returned as NaN; and a helper copy_with(**changes) returning a new Frame.

Tests: a valid frame passes; each validation failure raises.
```

## Prompt 2: coords.py (Emin)

```text
Write coords.py: turn user input into a sky position.

1. parse_position(ra, dec) accepting decimal degrees (133.795, -7.245) and sexagesimal in both styles ("08h55m10.831s" with "-07d14m42.53s", and "08 55 10.831" with "-07 14 42.53"). Treat a bare sexagesimal RA as hours. Enforce 0 <= RA < 360 and -90 <= Dec <= 90 with clear ValueError messages. Return decimal degrees (ICRS).
2. resolve_name(name): resolve via astropy SkyCoord.from_name (SIMBAD/CDS), with a small offline table so the demo works without internet for: WISE 0855-0714, Barnard's Star, Epsilon Indi B, RW Aurigae, R Draconis. For the offline table, define the fields (ra, dec, pmra, pmdec, reference epoch) but leave the numeric values as None; I will fill them in from the literature. Never invent coordinates or proper motions.
3. Proper-motion awareness: position_at_epoch(ra, dec, pmra, pmdec, ref_epoch, target_epoch) applying linear motion (pmra means mu_alpha*cos(dec)). resolve_name returns a ResolvedTarget (ra, dec, source, pm_known, warnings). When the target has known proper motion above 1 arcsec per year, return the position propagated to the SPHEREx survey epoch (default 2026.0, configurable) and add a warning saying so. For raw coordinates typed by the user, expose high_pm_hint(fov_arcsec) so the UI can recommend a larger field of view for fast movers.
4. Target profiles: a small dict mapping target class to a suggested detector (for example cold brown dwarf: 5, M dwarf: 2, T Tauri star: 3), used only as a default the user can override. Document that this is a heuristic.
5. No network calls at import time. Network failures fall back to the offline table or raise a clear error.

Tests (pytest, network mocked): decimal and both sexagesimal formats give the same position for the challenge's WISE 0855 coordinates (RA 08h55m10.831s, Dec -07d14m42.53s); out-of-range inputs raise; proper-motion propagation matches a hand-computed value; offline lookup works with the network disabled; an unknown name raises a clear error.
```

## Prompt 3: flags.py (Luka)

Before sending, open one real cutout, print the FLAGS header's MP\_\* keywords, and paste the bit table into the prompt. Do not let the AI write the bit meanings from memory.

```text
Write flags.py: decode the SPHEREx FLAGS bitmask.

Do NOT guess bit meanings. Use exactly this table, taken from the FLAGS extension header (MP_* keywords) and Table 8 of the SPHEREx Explanatory Supplement:

[PASTE THE BIT TABLE HERE]

Requirements:
1. FLAG_BITS: dict of name to bit number, from my table.
2. Groups, with a comment giving the rationale for each: DETECTOR_DEFECTS (permanent hardware problems, safe to mask), UNRELIABLE_CORE (saturated, non-linear or persistent pixels whose position is fine but flux is unreliable), TRANSIENTS, INFORMATION_ONLY (never mask; includes SOURCE and FULLSAMPLE).
3. flags_to_mask(flags, names=DETECTOR_DEFECTS) returning a bool array; decode_flags(value) returning the flag names set for one pixel; summarize(flags) returning counts per flag.
4. Provide no function that masks all non-zero flags.

Tests: single-bit and multi-bit values; a SOURCE-only pixel is not masked; a pixel with the hot-pixel flag is masked; uint16, int32 and float inputs; shape preserved.
```

## Prompt 4: data.py (Umair)

```text
Write data.py: fetch SPHEREx cutouts from IRSA, cache them, and return Frame objects.

Service facts (verify against IRSA's SPHEREx cutouts tutorial and tell me if any detail looks wrong): IRSA TAP, tables spherex.artifact and spherex.plane joined on planeid; select the artifact uri, the plane's time_bounds_lower (MJD) and energy_bandpassname; filter with CONTAINS(POINT('ICRS', ra, dec), plane poly). The cutout URL has the form https://irsa.ipac.caltech.edu/<uri>?center=<ra>,<dec>d&size=<size_deg>

Requirements:
1. query_images(ra, dec) using astroquery.ipac.irsa Irsa.query_tap (or pyvo), returning rows of (uri, mjd, bandpass). Raise a clear NoCoverage error when nothing is returned.
2. download_cutout(url, dest, retries=4) with back-off between retries. Keep only PRIMARY, IMAGE, FLAGS, VARIANCE, ZODI and WCS-WAVE. Write to a temporary file, then rename (atomic). Return a success bool.
3. Download in parallel with at most 5 workers and report progress. Individual failures are reported but not fatal as long as some frames succeed.
4. Cache as cache/<key>/NNNN.fits plus index.csv (file, mjd, bandpass, uri). The key comes from the rounded RA, Dec and the cutout size bucket (120, 240, 480, 600 arcsec). If the index is missing or damaged, rebuild it from the FITS headers. Serve a smaller field of view by cropping a cached larger cutout with astropy Cutout2D.
5. frame_from_hdul(hdul, ra, dec, mjd, uri): read layers by name; mask = defect flags | non-finite pixels | non-positive variance; unreliable = the UNRELIABLE_CORE flags; detector from the header; wavelength from the WCS-WAVE spectral WCS evaluated at the target pixel; processing version parsed from the filename; return a validated Frame.
6. Modes via the SPHX_MODE environment variable: cache (offline; clear error if not cached), live (query IRSA), auto (cache first, then IRSA), synthetic (explicit only: generates frames with a moving source and wavelength-dependent brightness, with meta["synthetic"] = True on every Frame). In auto mode, if IRSA is unreachable, raise a clear error. NEVER fall back to synthetic automatically.
7. get_frames(ra, dec, fov) returning a tuple of Frames sorted by MJD, with in-process caching.
8. scripts/fetch_cutouts.py: a CLI that fetches and caches a target by name or coordinates, so I can pre-download demo fields.

Tests, with no network, using small FITS files built inside the tests: reading by name, flag masking, damaged-index recovery, cropping geometry, mode selection, no silent synthetic fallback, and retry logic with a mocked failing downloader.
```

## Prompt 5: processing.py (Aghamusa)

```text
Write processing.py: turn raw Frames into comparable, display-ready images.

1. clean_frame(frame): subtract ZODI pixel by pixel when present; then subtract a 3-sigma-clipped median of the unmasked pixels as a local background; inpaint masked pixels with a local median for display only (keep the mask); return a new Frame and never mutate the input.
2. group_frames(frames, detector=None, wmin=None, wmax=None, max_width=0.10): choose the detector (default: the one with the most frames). When no window is given, choose the narrowest window of width <= max_width micrometres that contains the most distinct epochs and then the most frames. Return the chosen frames plus warnings (fewer than 3 frames, window forced wider than max_width, mixed processing versions).
3. split_epochs(frames, gap_days=30.0): assign an epoch index from MJD gaps; return per-epoch frame counts, start and end MJD, and date strings.
4. align_frames(frames): reproject all frames onto one common celestial grid centred on the target, north up and east left (astropy TAN WCS, about 6.15 arcsec per pixel, reproject_interp). Reproject the variance too and document the approximation used. Raise if a frame has no WCS.
5. stack_frames(frames): inverse-variance weighted coadd with MAD sigma clipping across frames per pixel; return the image, the variance, and the number of frames contributing per pixel.
6. render_png(image, stretch): percentile stretch with optional asinh, returning PNG bytes. Compute one stretch for a whole sequence and reuse it for every frame, so blinking is not disturbed by changing contrast. Expose the stretch values.

Tests with synthetic frames: zodi removal; background level near zero; grouping picks the intended window; epoch splitting at the 30-day gap; stack noise falling roughly as 1/sqrt(N) on simulated Gaussian noise; alignment preserves a source position under a rotated WCS; the PNG stretch is identical across a sequence.
```

## Prompt 6: app.py (Shayan)

```text
Write app.py (FastAPI) exposing the core as an API. All parameters are query strings so every view is shareable by URL: target, ra, dec, fov (30 to 600 arcsec, default 120, larger default for known fast movers), detector (1 to 6), wmin, wmax, mode (blink or stack).

Endpoints:
- GET /api/resolve: name or coordinates in; ra, dec and warnings out (including proper-motion notes).
- GET /api/meta: runs get_frames, group_frames and split_epochs; returns the resolved position, fov, detector, wavelength window, a per-frame list (index, mjd, date, wavelength, epoch, version), the epoch summary, warnings, data_source (archive, cache or synthetic), n_frames, and the detector wavelength ranges.
- GET /api/image.png?index=N: PNG of cleaned, aligned frame N (blink), or of the stack (mode=stack), using the shared stretch.
- GET /health.

Requirements: cache heavy work in-process, keyed by the full parameter set; blocking work must not stall the event loop; clear error bodies with status codes (400 bad input, 404 no coverage, 502 IRSA unreachable); no endpoint returns synthetic data unless SPHX_MODE=synthetic, and then every response says so; serve static/index.html at / and static assets; CORS closed by default.

Tests with FastAPI TestClient and synthetic FITS fixtures: every endpoint, every error code, the shared-stretch property, and parameter validation.
```

## Prompt 7: frontend, static/index.html (Shayan)

```text
Write static/index.html: a single file, vanilla JS, no build step, responsive, readable on a projector.

1. Input panel: target name OR RA/Dec (decimal or sexagesimal); field-of-view slider (30 to 600 arcsec); detector selector (auto, then 1 to 6, each with its wavelength range); optional wavelength window; Blink/Stack toggle.
2. Blink viewer: preload all frame PNGs with a progress indicator; play and pause; step back and forward; speed slider (50 to 2000 ms per frame); scrubber. For each frame show date, MJD, epoch, wavelength and processing version.
3. Epoch timeline showing the observation passes and frame counts.
4. Overlays: compass (north up, east left), a scale bar in arcseconds computed from the field of view, and a crosshair at the field centre. Instruments must not cover the image.
5. An always-visible status banner: data source (archive, cache or synthetic), number of frames, detector and wavelength window, and every API warning in plain language. If the data is synthetic, show a prominent "SYNTHETIC DATA" label.
6. Shareable URL: all parameters synced to the query string and restored on load.
7. States: loading with progress; empty ("no SPHEREx coverage here"); error with a retry button. Keyboard-accessible controls, labels and sufficient contrast.
8. A line of honest guidance: a visible change between frames is a candidate, not proof of motion, and frames in one sequence share a wavelength window to avoid colour flicker.

No network dependencies other than API calls to the same origin.
```

## Prompt 8: integration, README and self-review (Luka)

```text
Integration pass.
(a) Write tests/test_integration.py running the full path on synthetic FITS fixtures: coordinates, get_frames, group, epochs, align, stack, then PNG through the API.
(b) Write README.md: what it does; quick start (install, SPHX_MODE options, how to pre-fetch demo targets with scripts/fetch_cutouts.py); an API table; honest limitations (cutout size, no proper-motion fitting yet); and a section "Built during the hackathon" listing what was developed in the event. Keep it factual, with no unmeasured claims.
(c) Run ruff and pytest and list any failures.
(d) Review every module against the working rules: silent synthetic fallback, masking on flags != 0, index-based HDU access, fabricated data. Report findings before fixing anything.
```

## Checks you must do yourself between prompts

1. After Prompt 3, confirm the bit table against a real FLAGS header.
2. After Prompt 4, on the venue network early, run scripts/fetch\_cutouts.py for each demo target and for two fields you have not used before. Downloads are slow and IRSA is the single biggest schedule risk, so start them first.
3. After Prompt 5, check one real sequence by eye: the wavelength window is narrow, background is flat, and a known mover such as WISE 0855 visibly shifts between epochs.
4. After Prompt 7, open the page on a laptop and a phone and test a shared URL.
5. Enter the challenge's published WISE 0855 coordinates directly. If the field looks empty, the proper-motion handling is not working.

## What to build next: Phase 2

Three features turn a working viewer into a scientific result. Build them in this order, and stop adding features at hour 40.

| # | Feature | Why it matters to judges | Lead | Support |
| --- | --- | --- | --- | --- |
| 1 | Colour-flicker experiment | Gives measured evidence for the wavelength-window rule that the whole design depends on | Aghamusa | Umair (data) |
| 2 | Motion and parallax measurement, validated on benchmark stars | Turns 'it visibly moves' into a number with an honest uncertainty | Emin | Aghamusa (error model), Shayan (UI) |
| 3 | Blind mover search | Finds moving objects in a field without being told the target, which fits the Planet X theme | Aghamusa, after feature 1 | Shayan (UI), Umair (control fields), Luka (review) |

Hosted deployment and the submission assets are not optional extras; they are in the release checklist at the end.

## Phase 2 dependencies

Features 2 and 3 share only `processing.py`, so neither waits for the other. Everyone starts Phase 2 once Gate 4 (core works on real data) is passed, except Umair, who starts fetching data earlier.

| Person | Phase 2 work | Starts when | Blocked by |
| --- | --- | --- | --- |
| Luka | Reviews, `docs/api_contract.md` updates, README results, release | Hour 14 | Gate 4 |
| Aghamusa | Feature 1, then feature 3 | Hour 14 | Umair's cached flicker fields |
| Emin | Feature 2 | Hour 14 | Gate 4; Umair's cached benchmark fields; `data/benchmarks.csv` filled |
| Umair | Fetch flicker, benchmark and control fields; hosted deployment | Hour 12 | Gate 3 (real data flows through the API) |
| Shayan | UI for features 2 and 3 against mock JSON, then the real endpoints | Hour 14 | The JSON shapes written in Prompts 11 and 13 |

## Phase 2 timeline

Hours count from the start of the event, continuing the core timeline above.

| Hours | Who | What | Gate |
| --- | --- | --- | --- |
| 12 to 16 | Umair | Fetch and cache: three or more fields with bright stationary stars; the benchmark fields (Barnard's Star, WISE 0855, Epsilon Indi B, one stationary control); three control fields with no known fast movers | Fields cached and listed in the README |
| 12 to 16 | Emin, Luka | Fill `data/benchmarks.csv` and `data/flicker_stars.csv` from SIMBAD, Gaia and papers, with a citation per row | Literature values recorded |
| 14 to 22 | Aghamusa | Feature 1 (Prompt 9) | Gate 5: flicker table and figures; `max_width` default set from data |
| 14 to 24 | Emin | Feature 2 (Prompt 10) | Gate 6: motion fit validated; results table generated by script |
| 14 to 24 | Shayan | Prompt 11 UI against mock JSON |  |
| 22 to 32 | Aghamusa | Feature 3 (Prompt 12) | Gate 7: movers recovered on known fields; false-candidate rate measured |
| 22 to 32 | Umair | Prompt 14: hosted deployment, then control-field runs for Aghamusa | Hosted copy answers /health |
| 24 to 34 | Shayan | Prompts 11 and 13 against the real endpoints |  |
| 32 to 36 | Luka, everyone | Integration, README results tables, limitations section |  |
| 36 to 40 | Everyone | Bug fixes only; final hosted deployment | Gate 8: feature freeze at hour 40 |
| 40 to 48 | Everyone | Demo video, write-up, rehearsal; submit with a margin |  |

## Phase 2 context (paste after Prompt 0 in every Phase 2 session)

```text
Phase 2 context. The core exists and its tests pass: frame.py (Frame), coords.py, flags.py, data.py (get_frames), processing.py (clean_frame, group_frames, split_epochs, align_frames, stack_frames, render_png), app.py, static/index.html. Do not modify these modules unless I ask; add new modules and call the existing interfaces.

Rules for Phase 2, in addition to the Prompt 0 rules:
1. Every number we show must come from a script I can re-run, writing to results/ along with the command and the data versions used. No hand-edited results.
2. Decide pass and fail criteria before running anything and write them in the module docstring. Report failures and uninformative results as they are. Never choose criteria after seeing results.
3. Report an uncertainty with every measurement. If reduced chi-squared exceeds 1, inflate the uncertainties and say so.
4. When a result disagrees with the literature, investigate systematics (WCS errors between frames, wavelength-dependent centroid shifts, binary photocentre, parallax, saturated or masked pixels) and report what you find. Do not tune parameters to match.
5. Literature values (positions, proper motions, parallaxes) come only from data/benchmarks.csv, which the team fills from SIMBAD, Gaia or papers with citations. Do not supply them from memory.
6. Add tests with synthetic frames that have a known answer.

Acknowledge in two lines, then wait for my module request.
```

## Prompt 9: colour-flicker experiment (Aghamusa)

Treat any figure you remember about how much stationary stars vary with wavelength as a hypothesis; this experiment must measure it.

```text
Write experiments/flicker.py, which measures how much the brightness of stationary stars varies between SPHEREx exposures as a function of the wavelength difference. Our design assumes frames must share a narrow wavelength window to be compared; this experiment must measure whether that is true and how narrow the window has to be. Do not assume the answer.

Inputs: data/flicker_stars.csv (name, ra, dec, source): bright, isolated stars with negligible proper motion that I fill from Gaia and SIMBAD; cached fields from data.get_frames.

For each star and each detector:
1. Collect all frames containing the star and clean them with processing.clean_frame.
2. Aperture photometry at the star's position from each frame's own WCS: circular aperture (radius is a parameter, default 2 pixels), annulus background, flux uncertainty from the VARIANCE layer. Skip frames where the aperture touches masked or unreliable pixels, and count the skipped frames.
3. Plot (matplotlib, plain black and white) flux normalised by the star's median against wavelength, one panel per star per detector.
4. For window widths w from 0.02 to 0.40 micrometres, compute the fractional flux scatter among frame pairs whose wavelengths differ by less than w, subtract the expected photometric noise in quadrature, and plot the excess scatter against w with uncertainty bands.
5. Check that the effect is wavelength and not time: repeat the analysis against observation-time difference and show both.
6. Write results/flicker.json (all numbers, the star list, aperture settings, frame counts, data versions) and results/flicker_*.png.
7. Report the largest window width at which the excess scatter stays below a threshold parameter (default 5 percent) and print the recommended value for the max_width default of processing.group_frames, with its justification. State plainly if the data do not support a single value.

Tests: synthetic stars with a known flux-wavelength slope, expected scatter recovered within tolerance; skipped-frame counting; no result written if fewer than 3 stars have 5 or more usable frames.
```

## Prompt 10: motion and parallax fit (Emin)

Benchmark targets: Barnard's Star (very fast, about 10 arcsec per year), WISE 0855 (about 8 arcsec per year, parallax about 0.45 arcsec), Epsilon Indi B, and a stationary star as a control. These numbers are for orientation only; record the exact values with citations in `data/benchmarks.csv`.

```text
Write motion.py, scripts/validate_motion.py and tests. motion.py measures the position of a source in each epoch and fits proper motion and parallax.

Inputs: Frames from data.get_frames, grouped with processing.group_frames and split_epochs; a target position from coords.

1. Centroiding: on the cleaned, aligned frames, compute a background-subtracted, flux-weighted centroid inside a radius around the expected position, iterating with a shrinking window. Use pixels in Frame.unreliable for the centroid but not for flux. Estimate the centroid uncertainty analytically from the VARIANCE layer and check it with a Monte Carlo in the tests. Reject frames below a signal-to-noise threshold (parameter) and report how many were rejected.
2. Per-epoch position: combine the centroids within each epoch with inverse-variance weights and convert to sky coordinates through the WCS. Record each epoch's mean observation time.
3. Fit: position(t) = position at t0 + proper motion * (t - t0) + parallax * parallax_factor(t), where parallax_factor comes from the Earth's barycentric position (astropy) projected on RA and Dec. Weighted least squares with parameters RA0, Dec0, pm_ra (arcsec per year, including cos dec), pm_dec and parallax. With fewer than 3 epochs, parallax and proper motion are degenerate: fit proper motion only, optionally with parallax fixed from data/benchmarks.csv, and say so in the output.
4. Outputs: proper-motion magnitude and uncertainty, position angle and uncertainty, parallax and uncertainty (if fitted), reduced chi-squared, number of epochs and frames, motion significance (|pm| / sigma), the covariance matrix, and warnings. If reduced chi-squared > 1, scale the uncertainties by sqrt(chi-squared) and set a flag.
5. scripts/validate_motion.py: for each target in data/benchmarks.csv (name, ra, dec, literature pm and parallax, citation), run the fit and write results/motion_validation.csv and a markdown table. Fix these criteria in the script's docstring before running: AGREE if |measured - literature| < 3 sigma AND sigma < 0.3 * literature pm; UNINFORMATIVE if sigma >= 0.3 * literature pm (this is not a pass); DISAGREE otherwise. Include a stationary star, which must give a proper motion consistent with zero.

Tests with synthetic frames: a source moving at a known proper motion, with and without parallax, recovered within 3 sigma over 200 noise realisations; the degenerate two-epoch case handled and flagged; unreliable pixels not used for flux.
```

## Prompt 11: motion endpoint and panel (Shayan)

```text
Add the motion result to the API and the UI.

Backend (app.py): GET /api/motion with the same query parameters as /api/meta, returning JSON from motion.py: per-epoch centroids (epoch, mjd, ra, dec, sigma_arcsec, n_frames); the fit (pm_arcsec_per_yr, pm_sigma, position_angle_deg, pa_sigma, parallax and its sigma or null, chi2_reduced, n_epochs, significance, uncertainty_scaled); warnings; and, if the target is in data/benchmarks.csv, the literature values and the AGREE, UNINFORMATIVE or DISAGREE verdict. Errors: 404 for no coverage, 422 for too few epochs or frames.

Frontend (static/index.html): a Motion panel that (a) overlays the per-epoch centroids on the image with a vector arrow and error circles, (b) shows a table of the fit values with uncertainties and the verdict, (c) shows warnings in plain language, and (d) states in one line that a significance below 3 means the motion is not established.

Add the endpoint to docs/api_contract.md. Tests with TestClient and a mocked motion module.
```

## Prompt 12: blind mover search (Aghamusa)

```text
Write movers.py: a blind search for moving point sources in a field, with no target name given.

Input: ra, dec, fov (default 480 arcsec), detector and wavelength window; Frames from data.get_frames, grouped and split into epochs with processing.

1. For each epoch, build the stack with processing.stack_frames and a noise-normalised map (image divided by its propagated sigma). Detect local maxima above a threshold (default 5 sigma) with a minimum separation of 2 pixels, and measure each source's centroid, flux and signal-to-noise. Exclude sources within 3 pixels of the cutout edge or touching masked or unreliable pixels, and record why.
2. Cross-match sources between epochs with a search radius R = max_pm * baseline (max_pm default 15 arcsec per year; baseline from the epoch times). Classify each match: stationary (offset consistent with zero within 3 sigma), mover candidate (significant offset), or unmatched (present in one epoch only: possible transient, artefact, or a mover that left the field).
3. Estimate the chance-coincidence rate: shift epoch B by random offsets larger than R, repeat the matching 100 times, and report the expected number of false mover candidates for this field.
4. Output a ranked list (CSV and JSON): id, ra and dec in each epoch, offset in arcsec, proper motion and position angle with uncertainties, signal-to-noise in each epoch, motion significance, and quality flags. Every output must state that these are candidates, not detections.
5. scripts/validate_movers.py: run on (a) fields containing known movers (Barnard's Star, WISE 0855) without telling the search the target, and report whether each is recovered and at what rank; (b) at least three control fields with no known high-proper-motion object, and report the false-candidate counts. Fix the criteria in the docstring first, and report failures as they are.

Tests with synthetic fields: one injected mover recovered; stationary stars not flagged; edge sources excluded; the random-shift false-match estimate close to the analytic expectation for a known source density.
```

## Prompt 13: mover endpoint and overlay (Shayan)

```text
Add the mover search to the API and the UI.

Backend: GET /api/movers with the same parameters as /api/meta plus threshold (3 to 10 sigma) and max_pm; return the ranked candidates, the false-match estimate, per-epoch source counts and warnings.

Frontend: a Candidates layer on the blink viewer that circles each candidate in each epoch and connects the two positions; a sortable candidate table; clicking a row centres and zooms on it; a button on each candidate that opens its own short blink. Show the false-match estimate and the line 'Candidates are not confirmed detections' above the table. Add loading and empty states ('no candidates above threshold').

Update docs/api_contract.md. Tests with TestClient and a mocked movers module.
```

## Prompt 14: hosted deployment (Umair)

Replace \[HOST\] with Fly.io, Render or Hugging Face Spaces before sending.

```text
Prepare a hosted deployment. Write a Dockerfile (python slim image, non-root user, uvicorn on $PORT), a .dockerignore, and a one-command local run in the README.

Behaviour: SPHX_MODE=auto; a bundled cache of the demo fields (target size at most 50 MB; measure and report the real size); a /health endpoint that also reports the data mode and the cache size; request timeouts and a concurrency limit on routes that call IRSA, so one slow query cannot block the app; a clear user-facing message when IRSA is unreachable.

Write deployment instructions for [HOST], and a smoke-test script that checks /health, /api/meta, /api/image.png and /api/motion on the deployed URL. No secrets in the image or the repo.
```

## Release checklist

1. Umair: the hosted deployment is live, and the URL has been tested from a phone and a different network.
2. Luka: README results tables are generated by scripts with no hand-edited numbers, and include a limitations section and a 'Built during the hackathon' section.
3. Everyone: feature freeze at hour 40; after that, bug fixes only.
4. Luka: read the Submission Guide for required fields, video length and format, AI-use disclosure and what must be public, and prepare all of it by hour 36.
5. Luka and Shayan: the demo video leads with the live URL and one real result (a blink of Barnard's Star or WISE 0855), then the validation table, then one honest limitation. Everyone rehearses it once.
6. Shayan: the team page has real bios.
7. Everyone: submit with a margin of several hours; the Submit for Judging button is final.
