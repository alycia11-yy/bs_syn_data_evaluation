# Stage 2 on the reviewers' machines: TODO

Each reviewer rates on the same machine they used for Stage 1, running the website locally.
Those machines already have the repository, `config/users.json` and the Stage 1 data. **Leave all of that where
it is.** Stage 2 only adds three things:

| What | From your Mac (`/Users/jiayulim/Documents/GitHub/doctamper_eval/…`) | To the reviewer's machine (`<repo>/…`) |
|---|---|---|
| the updated code | `git push` after you commit | `git pull` in `<repo>` |
| that reviewer's pages: 1,000 images + 1,000 masks, `manifest.jsonl`, `assignment.csv`, `checksums.sha256` (≈230 MB) | `data/stage2_split/reviewerN/` | `data/stage2_split/reviewerN/` |
| the 15 practice pages + your answers (`reference.json`) | `data/stage2/practice/` | `data/stage2/practice/` |
| the reference PDF | `results/stage2_reference/reference_examples_jiayu_<date>.pdf` | `results/stage2_reference/` (or send the file) |

`<repo>` is the `doctamper_eval` folder on that machine; `N` is 1–4. The 15 practice pages and the PDF are the
same for everyone; the `reviewerN` folder is different for each person.

**Never copy:** another reviewer's folder, `data/stage2/reference/` (your own answers), anything from
`data/stage2/outputs/` or `data/stage2/plans/`, or your `config/users.json`.

## A. On your Mac, once

- [ ] **A1. Commit and push the code.**
  The changed files are:
  - `study/page.py`, `study/practice.py`, `study/reference.py` and `study_app.py`;
  - `scripts/make_training.py` and `scripts/reference_pdf.py`;
  - `config/training.json` and `config/datasets.json`;
  - `docs/` and `README.md`;
  - `tests/test_pipeline.py`.

  Without this push, a `reviewer` account gets "Not a rater account" on the reviewer's machine.
- [ ] **A2. Check the practice pages.** `python scripts/make_training.py` should print `reference examples: 15 pages`.
  They are already built, and a rerun gives the same pages.
- [ ] **A3. Make the reference PDF.** Run `python study_app.py`, open http://127.0.0.1:8080 and sign in as
  `jiayu`. You land on the admin page.
  1. On the **Reference examples** tab, press **Start rating** (or click a row to rate one page). Rate the 15
     pages the way a reviewer would. Each answer is saved straight away.
  2. If a page already has an answer, you can **Keep it** or rate it again.
  3. The tab shows "n of 15 pages rated" and your answer on each page.
  4. When all 15 are rated, press **Save reference PDF** on that tab, or at the end of the pages.
     It writes `results/stage2_reference/reference_examples_jiayu_<date>.pdf`, and the browser also downloads it.
     It also writes `data/stage2/practice/reference.json`: your answers, which the reviewers see after each
     practice page.

  There is no expected score. Your answers are the reference, and each one is described by its measures:
  the score, clicks on or off the edit, the outcome and the edit visibility (doc 12 §2).
  Do this before A4, so the package carries `reference.json`. If you rerun `make_training.py` later, it deletes `reference.json`; press **Save reference PDF** again afterwards.
- [ ] **A4. One package per reviewer**, from the repository folder. Set `PDF` to the final file from A3:

```bash
cd /Users/jiayulim/Documents/GitHub/doctamper_eval
PDF=results/stage2_reference/reference_examples_jiayu_<date>.pdf
for n in 1 2 3 4; do
  tar -czf ~/Desktop/stage2_reviewer$n.tgz data/stage2_split/reviewer$n data/stage2/practice "$PDF"
done
tar -tzf ~/Desktop/stage2_reviewer1.tgz | grep -c "/images/"     # 1000
```

  Give `stage2_reviewer<N>.tgz` only to reviewer N, on a USB drive or the internal share (it holds real Set 1–2 pages).

## B. On each reviewer's machine (reviewer N)

- [ ] **B1. Update the code.** Run `cd <repo>`, then `git pull`.
  If that machine has no git remote, copy the repository files from your Mac instead: everything except `data/`, `results/` and `config/users.json`.
- [ ] **B2. Set aside earlier Stage 2 test files, if there are any.**
  Run `ls data/stage2 data/stage2_split`. If either folder exists from a test, rename it so nothing old is counted:

```bash
mkdir -p data/_old && mv data/stage2 data/_old/stage2_before_study 2>/dev/null; mv data/stage2_split data/_old/stage2_split_before_study 2>/dev/null; true
```

- [ ] **B3. Unpack** from `<repo>`. Run `tar -xzf /path/to/stage2_reviewerN.tgz`; it unpacks into the paths in the table above.
- [ ] **B4. Check.**

```bash
ls data/stage2_split                                   # only: reviewerN
ls data/stage2/practice                                 # p01 … p15, masks, originals, practice.json, reference.json
python -c "import hashlib,pathlib; r=pathlib.Path('data/stage2_split/reviewerN'); rows=[l.split('  ',1) for l in (r/'checksums.sha256').read_text().splitlines()]; bad=[f for h,f in rows if hashlib.sha256((r/f).read_bytes()).hexdigest()!=h]; print(len(rows),'files,', 'OK' if not bad else f'{len(bad)} differ, e.g. {bad[:3]}')"
#   -> 2000 files, OK
python -c "import json; print(json.load(open('config/users.json'))['reviewerN']['role'])"   # reviewer (prints no password)
python -m pip install -r requirements.txt               # already done for Stage 1; harmless
```

- [ ] **B5. Start the website.** Run `python study_app.py`. The log must show these two lines:
  - `Folder mode: reviewerN -> 1000 images (+30 repeats, 0 controls)`;
  - `Practice: 15 image(s) from practice.json`.

  Then open http://127.0.0.1:8080.
- [ ] **B6. The reviewer:**
  1. Reads `docs/rater_guide.md`.
  2. Signs in with **their own** account (`reviewerN`, the Stage 1 password).
  3. Opens **Practice** and rates the 15 pages. The answer is shown after each one, and nothing is saved.
  4. Reads the reference PDF.
  5. Presses **Go to the study**.

  The top bar counts `n / 1030 done`. Every page is saved at once, so they can stop at any time; signing in again continues where they left off.
  Ask them to use only the website: the data folder has the masks and labels.

## C. Collect the ratings (at the end, or a snapshot at any time)

On the reviewer's machine, from `<repo>`:

```bash
tar -czf stage2_results_reviewerN.tgz data/stage2/outputs/reviewerN data/stage2/plans/reviewerN.json
```

On your Mac:

```bash
cd /Users/jiayulim/Documents/GitHub/doctamper_eval
tar -xzf ~/Downloads/stage2_results_reviewerN.tgz         # -> data/stage2/outputs/reviewerN/, data/stage2/plans/reviewerN.json
python -c "import json; print(len(json.load(open('data/stage2/outputs/reviewerN/ratings_latest.json'))), 'of 1030')"
```

Or open the **Reviewer progress** tab on the admin page and press **Refresh**. For each of reviewer1–4 it shows:
the status (no results yet / in progress / complete), how many pages are rated out of 1,030, main pages out of 1,000, repeats out of 30, the time of the last answer, and when the results file was last updated.
It shows counts only, never the answers. You can also collect a snapshot partway through to check progress.
Keep the reviewer's own copy until you have checked the count. Each package holds only that reviewer's files, so
unpacking one never overwrites another reviewer's results.

## Do not

- Do not run `split_stage2.py`, `make_training.py` or `create_user.py` on a reviewer's machine.
- Do not edit `config/users.json` anywhere. Each person signs in only with the account given to them.
- Do not rerun `split_stage2.py --force` on your Mac after the packages are made. The folders and `split.json`
  are the record of the draw (seed 20261006, manifest `ed9ef3b8…`).

## Still open (decide before the first reviewer starts)

- **Controls:** there are none now (`0 controls` in the log). If you add the 20 controls (`data/controls/crude`,
  `data/controls/clean`), add them before anyone starts and put `data/controls/` in every package. Adding them changes each reviewer's page list (1,050 instead of 1,030).
- **Pilot:** 50 pages with one person (doc 12 §5, step 4). Use a `pilot` account, or do it on a test copy.
- **Freeze:** tag the commit you pushed in A1, and keep `data/stage2_split/split.json` and the checksums.
