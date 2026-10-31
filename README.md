# doctamper_eval — human review websites for the document tampering datasets

Two websites for the four bank-statement datasets (Sets 1–4), following
`docs/12_realism_study_protocol.md` in `bs_syn_data`:

1. **Stage 1 · dataset check** — keep / reject every tampered image (obvious errors out);
2. **Stage 2 · realism study** — 4 raters judge 1,000 images drawn from what Stage 1 kept:
   a realism score and where they saw the edit, checked against the mask.

| | Stage 1 · author check | Stage 2 · realism study |
|---|---|---|
| who | you alone, or split between `reviewer1`–`reviewer4` (one folder each) | 4 raters (R1–R4), each with their own login |
| runs | `review_app.py` — locally, on your and the reviewers' machines | `study_app.py` — hosted (Docker image **doctamper-study**, §5), or locally to test |
| sees | **tampered images only**, with label boxes and the old → new text; authentic page + mask on demand | the final image only, under a random study id |
| answer | **Keep** / **Reject** (+ reason) | **No edit on this page**, or up to 3 clicks on the edits + score 1–5 (5 = genuine) |
| output | `data/stage1/selection.json` | `data/stage2/outputs/<rater>/ratings.jsonl` → `results/stage2/ratings.json` |

```text
set1..set4 ─► build_manifest ─► split_stage1 ─► reviewer1..4 run review_app locally
                                                   │ 4 × decisions.jsonl
             merge_stage1_decisions ◄──────────────┘ ─► selection.json
                       │
             split_stage2 (1,000: 500 real + 500 synthetic) ─► data/stage2_split/R1..R4
                       │
             package_stage2 ─► study_data/ ─► server: docker compose up (study_app)
                                                   │ stage2/outputs/<rater>/ratings.jsonl
             export_ratings (locally) ◄────────────┘ ─► results/stage2/ratings.csv
```

---

## 0) Install, and what git keeps

```bash
git clone https://github.com/ailensgroup/doctamper_eval.git && cd doctamper_eval
python3 -m venv .venv && source .venv/bin/activate     # or a conda env, Python ≥ 3.10
pip install -r requirements.txt                        # nicegui, bcrypt, pillow
```

Only code, configuration and documentation are in git. Everything below is ignored
(`.gitignore`) and kept out of Docker builds (`.dockerignore`), because it is private,
large, or generated — put it in place by hand on each machine:

| path | what | how it gets there |
|---|---|---|
| `data/set1` … `data/set4/` | the four datasets (`30_dataset/…`) | copy from the pipeline (`bs_syn_data`) |
| `data/stage1/manifest.jsonl` | list of every image | `python scripts/build_manifest.py` |
| `data/stage1/decisions/`, `selection.json` | Stage 1 decisions and result | written by `review_app.py` |
| `data/stage1_split/` | one folder per reviewer | `python scripts/split_stage1.py` |
| `data/stage2_split/` | one folder per rater | `python scripts/split_stage2.py` |
| `data/stage2/plans/`, `outputs/` (and `pool/`, `key/`) | Stage 2 orders, ratings (pool mode) | `study_app.py` (`build_study_pool.py`) |
| `config/users.json` (`users.test.json` …) | accounts (bcrypt hashes) | `python create_user.py …` |
| `results/` | exports | `export_stage1.py`, `export_ratings.py` |
| `config/*.demo.json`, `data/demo*` | fake data for trying things out | `make_demo_data.py` |
| `.nicegui/` | browser-session storage of the websites | created when an app runs |
| `study_data/`, `study_data.tar.gz` | the Stage 2 server folder (§5) | `python scripts/package_stage2.py` |
| `.env` | server secret for docker compose | copy `.env.example` |

Try everything on fake data first (no real documents involved):

```bash
python scripts/make_demo_data.py         # data/demo/set1..set4 + config/*.demo.json
export DATA_DIR=data/demo_run DATASETS_CONFIG=config/datasets.demo.json STUDY_CONFIG=config/study.demo.json
python review_app.py                     # http://127.0.0.1:8081
```

## 1) The data

```text
data/
  set1/30_dataset/images/   afbq_v1__012de85b7f_c0.jpg      authentic
                            afbq_v1__012de85b7f_t0_c0.jpg   tampered (t0 / t1)
  set1/30_dataset/masks/    <same stem>.png                 all black for authentic
  set1/30_dataset/labels.jsonl                              one row per image
  set2/ … set3/ … set4/     same layout (Sets 3–4 use anonymised hex names)
```

`config/datasets.json` is already set up for this layout. Everything comes from
`labels.jsonl` (matched on its `image` column):

| field in the app | from | used for |
|---|---|---|
| `label` | `label` | Stage 1 shows tampered only; Stage 2 strata |
| `doc_id` | Sets 1–2: the file name without `_t0`/`_t1` and `_c0`; Sets 3–4: `group` | whole-document reject; one version per document |
| `method` / `model` | `method`, `model` | balance: Sets 1/3 by method, Sets 2/4 by model |
| `edit_type` | `category` | info panel, key |
| `layout`, `capture`, `boxes` | same names (`capture` → its `condition`) | layout cap; label boxes |
| `image_id` | `internal_id` (Sets 1–2) | the `id` column of `decisions.csv` |
| views | `masks/<stem>.png`; the authentic page of the same document in the same set | Stage 1 reference panel |

`edits` / `box_info` (old → new text) are shown in Stage 1 under *What was changed*.
The config keys (`filename_pattern`, `metadata`, `view_templates`, `label_values`,
`boxes_format`, `exclude_patterns`, all per set or global) are documented at the top of
`core/datasets.py` if the layout ever changes.

```bash
python scripts/build_manifest.py         # -> data/stage1/manifest.jsonl (+ manifest_summary.json)
```

Every image gets the id `<set>/<file name>`, e.g. `set2/afbq_v1__012de85b7f_t0_c0.jpg`.
After changing `config/datasets.json`, rebuild (or start `review_app.py --rebuild-manifest`).
On the main machine keep this full manifest: the reviewer folders' own manifests leave
out the few documents that have no tampered version, whose authentic pages Stage 2
still needs.

## 2) Stage 1 — dataset check

`python review_app.py` → http://127.0.0.1:8081. **The mode follows what is on disk:**
with reviewer folders in `data/stage1_split/` it asks for a login and shows each account
its own folder; without them it is you alone, no login, every image.
`--no-login` forces the no-login view (your own machine only).

**Split between four reviewers** (each gets one folder; the folders are the truth):

```bash
python scripts/split_stage1.py --dry-run     # show the shares, copy nothing
python scripts/split_stage1.py               # data/stage1_split/reviewer1 … reviewer4
python create_user.py reviewer1 reviewer2 reviewer3 reviewer4 --role reviewer --generate
python create_user.py jiayu --role admin
python review_app.py                         # login; or the doctamper-review image (§5)
```

```text
data/stage1_split/
  split.json                         seed, reviewers, manifest checksum, counts
  reviewer1/manifest.jsonl           this folder's manifest rows: the folder works on its own
  reviewer1/assignment.csv           every file in the folder: set, file, label, doc, to_review
  reviewer1/checksums.sha256         shasum -a 256 -c checksums.sha256   (re-verify the copy)
  reviewer1/set1/30_dataset/{images,masks,labels.jsonl}   … set2, set3, set4
```

- **By document, seeded:** the authentic page, every tampered version in both paired
  sets, their masks and their `labels.jsonl` rows go to the same reviewer. Documents are
  shuffled with `--seed` (default 20261006) and dealt to whoever has the fewest images,
  so the four shares are equal in images (~2,445 each). Same manifest + names + seed =
  the same split, file for file.
- Every copy is checked against the manifest SHA-256. `--hardlink` uses no extra disk
  on the same volume.
- The app gives account `reviewerN` exactly the images in folder `reviewerN/` and serves
  them from there; a reviewer cannot load another reviewer's images. Move a file to
  another folder and it moves to that reviewer.
- `admin` sees everything, a progress chip per reviewer and an *Assigned to* filter.
- Decisions made before the split stay valid and show as done.
- Folders made before `manifest.jsonl` existed: `python scripts/split_stage1.py
  --write-manifests` adds it without copying anything.
- **The four reviewers must not be the Stage 2 raters.** Stage 1 shows the edit boxes,
  the masks and the old → new text; anyone who has seen them knows where the edits are
  (doc 12 §4: raters "who did not build the dataset").

**On each reviewer's own machine instead of a server:**

```text
doctamper_eval/                       git clone of this repository
  config/users.json                   the accounts file (it can hold just that reviewer)
  data/stage1_split/reviewer1/        only that reviewer's folder, copied as is
```

```bash
pip install -r requirements.txt
python review_app.py                  # http://127.0.0.1:8081, sign in as reviewer1
```

Nothing else is needed: the folder carries its own `manifest.jsonl`. Send each reviewer
only their own folder (one zip per reviewer), made *after* the folder has its
`manifest.jsonl`. Decisions are
saved on that machine in `data/stage1/`. When they are done, collect each reviewer's
`data/stage1/` folder and merge them on your machine:

```bash
python scripts/merge_stage1_decisions.py returned/reviewer1/stage1 returned/reviewer2/stage1 …
# or just the files they send back (any names):
python scripts/merge_stage1_decisions.py returned/reviewer*_decisions.jsonl
```

It appends every record to `data/stage1/decisions/` (running it twice adds nothing), the
newest decision per image wins by timestamp, decisions on images outside the reviewer's
folder are reported, and `selection.json` is rewritten. Then `export_stage1.py` as usual.
Sets 1–2 are real customer documents: copy them only to machines and people allowed
to hold them under the data agreement.

| key | action |
|---|---|
| `Y` / `K` | keep |
| `N` / `R`, then `1`–`6` | reject: wrong/garbled text · damaged page · label does not cover the edit · privacy leak · broken base document · other |
| `D` (while rejecting) | reject the **whole document** — every version, both paired sets |
| `A` | show / hide the authentic page and the mask under the tampered image |
| `←` `→` · `B` · `+` `−` | previous / next · label boxes on/off · zoom |

Only **tampered** images are shown (`review_only_tampered` in `config/datasets.json`).
Authentic images have nothing to check, so they count as kept — unless a
whole-document reject removes their document. Filters: set, status (pending / keep /
reject / all). Re-deciding an image replaces its earlier decision; a short message at
the bottom confirms every save. Keep on one version of a whole-document reject keeps
just that version; Keep on the image the document was rejected from lifts it for all.
*Reject obvious errors only — a merely noticeable edit is kept; Stage 2 measures how
noticeable.*

Every click is saved immediately:

```text
data/stage1/decisions/decisions.jsonl         every decision, append-only
data/stage1/decisions/decisions_latest.json   newest decision per image (by timestamp)
data/stage1/selection.json                    rewritten after each decision
```

```json
{
  "complete": false,
  "counts": {"set1": {"selected": 1201, "rejected": 40, "pending": 948, "total": 2189,
                      "authentic_auto_kept": 740}},
  "sets": {"set1": {"selected": ["000001_a.png", "..."], "rejected": ["..."], "pending": ["..."]}},
  "rejected_details": [{"id": "set1/000002_t1.png", "reason": "damaged_page", "scope": "image",
                        "decided_on": null, "note": ""}]
}
```

**Re-check (recommended, a week later):** `python review_app.py --recheck` shows a fixed
random 5 % of decided images without the earlier answers. Each reviewer re-checks their own; the admin gets 5 % of *each* reviewer's
decisions — an audit of agreement between you and every helper.

**Export for the pipeline:**

```bash
python scripts/export_stage1.py
# results/stage1/<set>/decisions.csv   id,decision,reason,reviewer,image,date
#   -> copy to data/sets/<set>/35_review/ and run: bash slurm/submit.sh 66_apply_review
# results/stage1/reject_counts.csv     per set × method × label × reason
# results/stage1/recheck.json          agreement + Cohen's kappa per reviewer: self + your audit
```

Then freeze the datasets (git tag + checksums, doc 12 §1.5) before Stage 2.

## 3) Stage 2 — the images for the raters

`config/study.json` holds the design: the six strata (Set 1/2/3/4 tampered 160 each,
real/synthetic authentic 180 each), `max_layout_share` 0.10, 30 repeats, pilot size 50,
`practice_size` 1, up to 3 clicks per page, zoom 100–200 %, `click_margin_fraction` 0.5,
seed. The draw:

- only images **kept** in Stage 1; one image per (side, document) over the whole pool;
- tampered strata first, balanced round-robin over method / model, ≤ 10 % per layout;
  authentic strata take documents not used above;
- 30 repeats (same image, new study id, at least 100 images later) and optional controls from
  `data/controls/crude/` (your crude edits, optional `masks/<stem>.png` or `boxes.json`)
  and `data/controls/clean/` (clean pages).

There are two ways to hand the draw to the website; `study_app.py` picks whichever is on
disk (rater folders first).

**A · rater folders — the same layout as Stage 1 (recommended):**

```bash
python scripts/split_stage2.py --dry-run     # show the draw per rater, copy nothing
python scripts/split_stage2.py               # data/stage2_split/R1 … R4
```

```text
data/stage2_split/
  split.json                       seed, mode, raters, counts, warnings of the draw
  R1/manifest.jsonl                the rows of these images (+ `stratum`)
  R1/assignment.csv                set, file, label, doc, method / model, stratum
  R1/checksums.sha256              shasum -a 256 -c checksums.sha256
  R1/set1/30_dataset/{images,masks,labels.jsonl}   … set2, set3, set4
```

- Folder name = account name. Each rater sees only the images in their folder.
- `--mode shared` (default): every rater gets the same images — required for agreement
  between raters (Krippendorff's alpha, ICC) and a mean score per image (doc 12 §3).
  `--mode split` divides the images between raters (same mix of strata each): fewer
  images per person, but one rating per image and no agreement statistics.
- **Localization uses the masks.** When a rating is saved, every click is checked
  against that image's mask: a hit when an edited pixel lies within half the height of
  the edited text (`click_margin_fraction`; 10 px when there are no label boxes). Each
  edited place (one label box per edited field) counts once: clicking both places of a
  two-edit page gives `localization_score` 1.0, one of them 0.5. The rating stores
  `clicks[].hit`, `regions_total`, `regions_found`, `localization_score`, `edit_found`
  (a click on an edit) and `false_alarm` (a click on nothing — every click on an
  authentic page). The rater never sees any of it.
- `split_stage2.py` also writes the **practice images**: `--practice N` (default
  `practice_size`) images **rejected in Stage 1** — unusable for the study anyway — with
  their masks, to `data/stage2/practice/` + `practice.json`.
- Raters see blinded study ids (`S` + 10 hex characters), never file names; masks and
  labels stay on the server. **Never send these folders to a rater.**

**B · one blinded pool** (`python scripts/build_study_pool.py`): images renamed
`data/stage2/pool/images/S0001.png …` (PNG re-encoded without metadata, JPEG as is), the
answer key in `data/stage2/key/` (keep private); localization is scored afterwards with
`export_ratings.py --with-key` against the label boxes.

**Test run before Stage 2 exists** — use the Stage 1 folders as stand-in rater folders,
with a separate accounts file and output folder so nothing real is touched:

```bash
USERS_FILE=config/users.test.json python create_user.py reviewer1 --role rater --generate
USERS_FILE=config/users.test.json python create_user.py jiayu --role admin
USERS_FILE=config/users.test.json STAGE2_SPLIT_DIR=data/stage1_split \
  STAGE2_DIR=data/stage2_test python study_app.py          # http://127.0.0.1:8080
```

The log warns that those folders hold several versions per document (they are Stage 1
folders); `split_stage2.py` never does that.

*Fallback (both ways):* if a side has no authentic/tampered labels in the manifest yet,
that side is a plain random draw of `fallback_per_side` (500) kept images, still one per
document. The script prints a warning.

### As set up for v1.0 (31 October 2026)

```bash
# data: the filtered dataset only (config/datasets.json reads set1..4/31_filtered/images)
python scripts/build_manifest.py              # 13,701 images: 2,113 / 1,246 / 6,604 / 3,738
# the draw, under the existing Stage 1 accounts (folder name = account name), fixed by seed + manifest
python scripts/split_stage2.py --raters reviewer1 reviewer2 reviewer3 reviewer4 --hardlink --practice 0
# training: 15 reference pages (synthetic, documents outside the pool), expected score + note per page
python scripts/make_training.py               # data/stage2/practice/ (p01–p15, masks/, originals/) + docs/rater_guide.md
```

- Accounts: `config/users.json` unchanged. `reviewer1`–`reviewer4` keep role `reviewer` and their
  passwords; in folder mode a `reviewer` account rates the folder of its name. `jiayu` (admin) sees /admin.
- Each account: the same 1,000 images + 30 repeats = 1,030 pages, own fixed order. Do not rerun
  `split_stage2.py --force` once rating has started: the pool must stay the one in `split.json`.
- Reference examples: the 15 pages and notes are fixed in `config/training.json`
  (`python scripts/make_training.py --candidates` draws new candidates, seeded). Sign in as `jiayu`: the
  admin page has two tabs. **Reference examples**: Start rating / click a row to (re-)rate a page; each answer
  is saved in `data/stage2/reference/jiayu/` (rating again replaces it, "Keep it" skips); when all 15 are rated,
  **Save reference PDF** → `results/stage2_reference/reference_examples_jiayu_<date>.pdf` (also downloaded) and
  `data/stage2/practice/reference.json` (shown to raters after each practice page). No expected score: the
  admin's answers are the reference, each described by its measures (`study/outcome.py`: score, clicks on / off
  the edit, outcome, edit visibility; bs_syn_data doc 12 §2 and §6, with worked examples).
  Same from the command line: `python scripts/reference_pdf.py`. Raters' practice answers are never saved.
  **Reviewer progress**: per account (reviewer1–4, plus anyone with a plan) rated / 1,030, main / 1,000,
  repeats / 30, last answer, results file updated — counts only, from `data/stage2/outputs/` (copy each
  reviewer's results in, then Refresh).
- Rater machines (each rater runs the website locally): `docs/stage2_reviewer_machines_TODO.md`.
- Test run without touching real outputs (plans, ratings, reference answers go to `data/stage2_test/`):

```bash
STAGE2_DIR=data/stage2_test python scripts/make_training.py    # practice pages for the test folder
STAGE2_DIR=data/stage2_test python study_app.py                # http://127.0.0.1:8080
```

## 4) Stage 2 — accounts and the study website

```bash
python create_user.py R1 R2 R3 R4 --role rater --generate   # prints each password once
python create_user.py pilot1 --role pilot --generate
python create_user.py jiayu --role admin
python study_app.py                                  # http://127.0.0.1:8080
```

`config/users.json` holds bcrypt hashes and is gitignored.

| role | sees |
|---|---|
| `rater` | all images of their folder (or the pool) + controls, in their own random order (seeded by username), as one list; repeats come at least 100 images after their original |
| `pilot` | 50 main images; stored with `role: pilot`, excluded from exports |
| `admin` | `/admin`: Reference examples tab (rate the practice pages → reference PDF) and Reviewer progress tab (done / total per account, last activity) — never the answers |

Per page the rater either presses **No edit on this page** (score 5, next page), or
**clicks every place they think was edited** (up to 3, the place they are most sure about
first; each click means "an edit is here", the bin icon removes one) and gives **one score
for the whole page** — not one per place; with several places marked, the strongest sign of
editing decides (ITU-T P.910 ACR + LROC design; bs_syn_data doc 12 §2) — on the full
scale — **5 genuine · 4 probably genuine · 3 unsure · 2 probably edited · 1 clearly
edited** (higher = more real). Where they clicked and how real the page looks are separate
answers: a rater may spot an edit that still looks fairly real.

**No sessions.** Every page is saved the moment it is answered; whenever the rater signs
in again they continue at the next unanswered page. No going back, no feedback.

**Practice round** (`/practice`, not scored, raters' answers not saved; a *Practice* button in the top
bar, and a hint on a rater's first page): the images of `data/stage2/practice/practice.json`
— Stage 1 rejects, written by `split_stage2.py` — or, when there is no such file (testing),
`practice_size` images picked at random from the rater folders. After each answer it shows
whether the page was edited, the real edit boxes, and which clicks hit (green) or missed
(red). Hand-made entries work too:

```json
{"items": [{"image": "p01.jpg", "mask": "masks/p01.png", "label": "tampered",
            "boxes": [[x0, y0, x1, y1]], "width": 2480, "note": "The balance was retyped."}]}
```

**Output** — one line per submitted image, click coordinates in image pixels:

```text
data/stage2/outputs/<rater>/ratings.jsonl
data/stage2/outputs/<rater>/ratings_latest.json
```

```json
{"rater": "R1", "role": "rater", "study_id": "S4649c22930", "no_traces": false,
 "clicks": [{"x": 381.0, "y": 243.7, "hit": true}, {"x": 1080.0, "y": 360.3, "hit": false}],
 "score": 4, "time_ms": 9312, "max_zoom": 150, "position": 9,
 "timestamp": "2026-10-20T03:12:09Z", "pool_version": "2db512f079cd",
 "item_id": "set4/eca60c388862f92c.png", "label": "tampered", "localization": "mask",
 "margin_px": 14.5, "regions_total": 1, "regions_found": 1, "localization_score": 1.0,
 "edit_found": true, "false_alarm": true}
```

The fields from `item_id` on are added in folder mode only.

```bash
python scripts/export_ratings.py              # results/stage2/ratings.json + ratings.csv
                                              #   folder mode: with set, label, method /
                                              #   model, layout, hits (keep private)
python scripts/export_ratings.py --with-key   # pool mode: + ratings_with_key.csv
```

## 5) Hosting Stage 2 (the only hosted website)

Stage 1 runs on your and the reviewers' own machines (`python review_app.py`). Only the
Stage 2 study is hosted: `docker-compose.yml` has one service, `study`
(`Dockerfile.study`, image `doctamper-study`, port 8080 on localhost for the reverse
proxy). Code comes from the image; data, accounts and ratings live in one folder,
**`study_data/`**, next to `docker-compose.yml` on the server.

**a. On your machine — from the four Stage 1 results to `study_data/`**

```bash
# the four files the reviewers send back (any names):
python scripts/merge_stage1_decisions.py returned/reviewer1_decisions.jsonl \
    returned/reviewer2_decisions.jsonl returned/reviewer3_decisions.jsonl \
    returned/reviewer4_decisions.jsonl          # -> data/stage1/selection.json
python scripts/export_stage1.py                 # results/stage1/ (for the pipeline)
python scripts/split_stage2.py --dry-run        # check the draw
python scripts/split_stage2.py --practice 5     # data/stage2_split/R1..R4 + data/stage2/practice
python create_user.py R1 R2 R3 R4 --role rater --generate   # passwords printed once
python create_user.py jiayu --role admin --generate
python scripts/package_stage2.py --archive      # -> study_data/ + study_data.tar.gz
```

`split_stage2.py` copies the drawn images out of `data/set1..4/30_dataset/` (with masks
and label rows) — nothing else from the datasets goes to the server.
`package_stage2.py` keeps only the Stage 2 accounts (`rater`, `pilot`, `admin`) and
never includes `data/stage2/key/`, `data/stage1/` or the full datasets:

```text
study_data/
  config/users.json        Stage 2 accounts (bcrypt hashes)
  config/study.json        the study settings, frozen with the data
  stage2_split/R1 … R4/    manifest.jsonl, assignment.csv, checksums.sha256,
                           set1..set4/30_dataset/{images,masks,labels.jsonl}
  stage2/practice/         practice.json, p01.png …, masks/
  stage2/plans/            empty — each rater's order, written on first sign-in
  stage2/outputs/          empty — <rater>/ratings.jsonl, written as raters go
  controls/                control pages (empty if none)
  PACKAGE.json             SHA-256 of every file, to check the upload
```

**b. On the server**

```bash
git clone git@github.com:ailensgroup/doctamper_eval.git && cd doctamper_eval
# upload study_data.tar.gz here, then:
tar xzf study_data.tar.gz
sudo chown -R 10001:10001 study_data          # the container runs as UID 10001
python3 scripts/check_study_data.py study_data   # preflight (stdlib only) -> "OK"
cp .env.example .env    # set NICEGUI_STORAGE_SECRET (python3 -c 'import secrets; print(secrets.token_hex(32))')
docker compose up -d --build
docker compose logs -f study    # "Folder mode: R1 -> … images", "Practice: N image(s)"
```

Put it behind the HTTPS reverse proxy (Traefik labels are in the compose file, commented);
the sign-in cookie is HTTPS-only. Only for a plain-HTTP test on an internal network set
`HTTPS_ONLY=0` in `.env`. If Docker Hub is blocked, set `BASE_IMAGE` in `.env` to a
`python:3.12-slim` from your own registry.

**c. While the study runs** — sign in as the admin: `/admin` shows done / total and the
last activity per rater (never the answers). Every answer is written at once to
`study_data/stage2/outputs/<rater>/ratings.jsonl` on the server, so restarting or
rebuilding the container loses nothing, and raters continue where they stopped.

**d. Afterwards — download and process locally**

```bash
scp -r server:doctamper_eval/study_data/stage2/outputs data/stage2/outputs
python scripts/export_ratings.py    # results/stage2/ratings.json + ratings.csv
```

`export_ratings.py` joins the image metadata from your local `data/stage2_split/`, so
keep that folder as it was when you packaged it.

Notes: Sets 1–2 are real customer documents — internal server, authorised staff only.
Stage 1 reviewers must not be Stage 2 raters (a `reviewer` account is turned away).
`Dockerfile.review` is not used by the compose file; it is only there should Stage 1 ever
need hosting.

## 6) Layout

```text
review_app.py              Stage 1 entry               study_app.py        Stage 2 entry
review/selection.py        decisions -> selection      study/sampling.py   pool draw (pure)
review/assign.py           reviewer split + folders    study/plan.py       per-rater order
review/state.py, page.py   Stage 1 state + page        study/ratings.py    scale + validation
core/settings.py           paths + env                 study/widget.py     click + score widget
core/datasets.py           config + folder scan        study/page.py       gate, practice, admin
core/manifest.py           manifest read/write         auth/               login, roles, middleware
core/jsonstore.py          append-only JSONL + latest  create_user.py      accounts
core/images.py, ui.py      image serving, shared look  study/folders.py    Stage 2 rater folders
core/folder_copy.py        copy + checksum helpers     study/localize.py   clicks vs mask
                                                       study/practice.py   practice images
config/                    datasets, study, users.example
scripts/   build_manifest · split_stage1 · merge_stage1_decisions · export_stage1
           split_stage2 · package_stage2 · check_study_data · build_study_pool
           export_ratings · make_demo_data
Dockerfile.study · docker-compose.yml (Stage 2 only) · .env.example · Dockerfile.review (unused)
tests/     python -m pytest        (pip install pytest)
```

| env var | default | |
|---|---|---|
| `DATA_DIR` | `./data` | everything under `stage1/`, `stage2/` |
| `DATASETS_CONFIG` / `STUDY_CONFIG` | `config/datasets.json` / `config/study.json` | |
| `USERS_FILE` | `config/users.json` | |
| `RESULTS_DIR` | `./results` | exports |
| `STAGE1_SPLIT_DIR` | `./data/stage1_split` | the reviewer folders (absent = no split) |
| `STAGE2_SPLIT_DIR` | `./data/stage2_split` | the rater folders (absent = pool mode) |
| `STAGE2_DIR` | `./data/stage2` | Stage 2 plans, ratings, pool, key, practice |
| `HOST` / `PORT` | `127.0.0.1` / `8080` | Stage 2; Stage 1 uses `--host` / `--port` (default 8081) |
| `APP_ENV` | `dev` | `production` requires `NICEGUI_STORAGE_SECRET`, HTTPS-only cookie |
| `HTTPS_ONLY` | `1` | `0` allows login over plain HTTP in production (internal network only) |
