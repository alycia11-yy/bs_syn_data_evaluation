# Document tampering — human review websites

Two websites for the four bank-statement datasets (Sets 1–4), following
`docs/12_realism_study_protocol.md` in `bs_syn_data`.

| | Stage 1 · author check | Stage 2 · realism study |
|---|---|---|
| who | you alone, or split between `reviewer1`–`reviewer4` (one folder each) | 4 raters (R1–R4), each with their own login |
| runs | `review_app.py` — locally, or as Docker image **bs-review** with logins | `study_app.py` — Docker image **bs-study**, login required |
| sees | **tampered images only**, with label boxes and the old → new text; authentic page + mask on demand | the final image only, under a random study id |
| answer | **Keep** / **Reject** (+ reason) | "no edit traces", or up to 3 clicks (edit / looks odd) + score 1–5 |
| output | `data/stage1/selection.json` | `data/stage2/outputs/<rater>/ratings.jsonl` → `results/stage2/ratings.json` |

```text
set1..set4 ──► build_manifest ──► review_app (Stage 1) ──► selection.json
                                                               │
                                       build_study_pool  ◄─────┘   (1,000: 500 real + 500 synthetic)
                                              │
                         pool/ (blinded) ─────┴───── key/ (answers, private)
                              │
                         study_app (Stage 2) ──► ratings ──► export_ratings
```

---

## 0) Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # nicegui, bcrypt, pillow
```

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
python review_app.py                         # login; or the bs-review Docker image (§5)
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
bs_syn_data_evaluation/               git clone of this repository
  config/users.json                   the accounts file (it can hold just that reviewer)
  data/stage1_split/reviewer1/        only that reviewer's folder, copied as is
```

```bash
pip install -r requirements.txt
python review_app.py                  # http://127.0.0.1:8081, sign in as reviewer1
```

Nothing else is needed: the folder carries its own `manifest.jsonl`. Decisions are
saved on that machine in `data/stage1/`. When they are done, collect each reviewer's
`data/stage1/` folder and merge them on your machine:

```bash
python scripts/merge_stage1_decisions.py returned/reviewer1/stage1 returned/reviewer2/stage1 …
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
reject / all). Re-deciding an image replaces its earlier decision. *Reject obvious errors only — a merely noticeable edit is
kept; Stage 2 measures how noticeable.*

Every click is saved immediately:

```text
data/stage1/decisions/decisions.jsonl         every decision, append-only
data/stage1/decisions/decisions_latest.json   latest decision per image
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

**Re-check (recommended, a week later):** `python review_app.py --recheck` shows a fixed random 5 % of decided images without the earlier
answers. Each reviewer re-checks their own; the admin gets 5 % of *each* reviewer's
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

## 3) Stage 2 — draw the pool

`config/study.json` holds the design: the six strata (Set 1/2/3/4 tampered 160 each,
real/synthetic authentic 180 each), `max_layout_share` 0.10, 30 repeats, session size 150,
pilot size 50, 3 clicks, zoom 100–200 %, seed.

```bash
python scripts/build_study_pool.py       # refuses while Stage 1 still has pending images
```

- only images **kept** in Stage 1; one image per (side, document) over the whole pool;
- tampered strata first, balanced round-robin over method / model, ≤ 10 % per layout;
  authentic strata take documents not used above;
- 30 repeats (same image, new study id, later session) and optional controls from
  `data/controls/crude/` (your crude edits) and `data/controls/clean/` (clean pages),
  each with an optional `boxes.json`;
- images are copied to `data/stage2/pool/images/S0001.png …` — PNG/TIFF re-encoded
  losslessly (drops embedded metadata), JPEG copied byte for byte;
- `data/stage2/pool/` is all the server needs. `data/stage2/key/` (study id → set, label,
  method, boxes; plus the frozen selection) stays with you.

*Fallback:* if a side has no authentic/tampered labels in the manifest yet, that side is a
plain random draw of `fallback_per_side` (500) kept images, still one per document. The
script prints a warning.

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
| `rater` | all main images + controls in their own random order (seeded by username), in sessions of 150; repeats in a later session than their original |
| `pilot` | 50 main images, one session; stored with `role: pilot`, excluded from exports |
| `admin` | `/admin`: progress per user (count, session, last activity) — never the answers |

Per image the rater either presses **I saw no edit traces** (score 5, next image), or clicks
up to 3 places, marks each **I saw an edit here** / **It just looks odd here**, and picks a
score that agrees with the clicks (edit → 1–2, only odd → 3–4). No going back, no feedback.
A session gate appears before every session; signing out and in again resumes exactly
where the rater stopped.

**Practice round** (`/practice`, not scored): put images in `data/stage2/practice/` with

```json
{"items": [{"image": "p01.png", "label": "tampered", "boxes": [[x0, y0, x1, y1]],
            "width": 1654, "note": "The balance was retyped."}]}
```

After each answer it shows the truth and the edit boxes.

**Output** — one line per submitted image, click coordinates in image pixels:

```text
data/stage2/outputs/<rater>/ratings.jsonl
data/stage2/outputs/<rater>/ratings_latest.json
```

```json
{"rater": "R1", "role": "rater", "study_id": "S0412", "no_traces": false,
 "clicks": [{"x": 512.0, "y": 88.5, "kind": "edit"}], "score": 2, "time_ms": 9312,
 "max_zoom": 150, "session": 3, "position": 41, "timestamp": "2026-10-20T03:12:09Z",
 "pool_version": "b2e7a551b056"}
```

```bash
python scripts/export_ratings.py              # results/stage2/ratings.json + ratings.csv
python scripts/export_ratings.py --with-key   # + ratings_with_key.csv (analyst only): set,
                                              #   label, method, layout, click hits, edit_found
```

## 5) Two websites, two Docker images, one repository

| image | Dockerfile | serves | mounts (from `SERVER_DIR`) |
|---|---|---|---|
| `bs-review` | `Dockerfile.review` | Stage 1, `review_app.py --users` | `stage1_split/` (ro), `stage1/` (decisions, rw), `config/users.json` |
| `bs-study` | `Dockerfile.study` | Stage 2, `study_app.py` | `stage2/pool/` (ro), `stage2/practice/` (ro), `stage2/plans/` + `stage2/outputs/` (rw), `config/users.json` |

```bash
# on your machine: make what the server needs
python scripts/build_manifest.py && python scripts/split_stage1.py
# copy to the server, e.g. SERVER_DIR=/home/ailens/bs_review
#   data/stage1_split/          -> $SERVER_DIR/stage1_split/
#   config/users.json           -> $SERVER_DIR/config/users.json
export SERVER_DIR=/home/ailens/bs_review
export NICEGUI_STORAGE_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
docker compose up -d --build review          # Stage 1  -> 127.0.0.1:8081
# … Stage 1 done: copy $SERVER_DIR/stage1 back (e.g. to returned/server/stage1), then locally:
python scripts/merge_stage1_decisions.py returned/server/stage1
python scripts/export_stage1.py && python scripts/build_study_pool.py
#   data/stage2/pool/ -> $SERVER_DIR/stage2/pool/   (never data/stage2/key/)
docker compose up -d --build study           # Stage 2  -> 127.0.0.1:8080
```

- One `config/users.json` serves both: `reviewer` accounts open only Stage 1, `rater` /
  `pilot` only Stage 2, `admin` both. A reviewer who signs in to Stage 2 is turned away.
- The containers run as UID 10001: `stage1/`, `stage2/plans/` and `stage2/outputs/` must
  be writable by it. Put both behind the usual HTTPS proxy (Traefik); in production the
  session cookie is HTTPS-only. On a plain-HTTP internal network set `HTTPS_ONLY=0`
  (passwords then travel unencrypted).
- Sets 1–2 are real customer documents: internal server only, authorised staff only.

## 6) Layout

```text
review_app.py            Stage 1 entry (local)            study_app.py   Stage 2 entry
core/settings.py         paths + env                      auth/          login, roles, middleware
core/datasets.py         config + folder scan             study/sampling.py  pool draw (pure)
core/manifest.py         manifest read/write              study/plan.py      per-rater order
core/jsonstore.py        append-only JSONL + latest map   study/ratings.py   scale + validation
core/images.py, ui.py    image serving, shared look       study/widget.py    click + score widget
review/selection.py      decisions -> selection (pure)
review/assign.py         reviewer split + reading the reviewer folders    study/page.py      gate, practice, admin
review/state.py, page.py Stage 1 state + page
scripts/  build_manifest · split_stage1 · merge_stage1_decisions · export_stage1 · build_study_pool · export_ratings · make_demo_data
Dockerfile.review · Dockerfile.study · docker-compose.yml (services review, study)
tests/    python -m pytest
```

| env var | default | |
|---|---|---|
| `DATA_DIR` | `./data` | everything under `stage1/`, `stage2/` |
| `DATASETS_CONFIG` / `STUDY_CONFIG` | `config/datasets.json` / `config/study.json` | |
| `USERS_FILE` | `config/users.json` | |
| `RESULTS_DIR` | `./results` | exports |
| `STAGE1_SPLIT_DIR` | `./data/stage1_split` | the reviewer folders (absent = no split) |
| `HOST` / `PORT` | `127.0.0.1` / `8080` | Stage 2; Stage 1 uses `--host` / `--port` (default 8081) |
| `APP_ENV` | `dev` | `production` requires `NICEGUI_STORAGE_SECRET`, HTTPS-only cookie |
| `HTTPS_ONLY` | `1` | `0` allows login over plain HTTP in production (internal network only) |
