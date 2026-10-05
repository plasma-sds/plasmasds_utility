# AGENTS.md

Instructions for coding agents working in this repository. Human contributors are welcome to follow them too.

Section 1 and the review lens in section 5 are specific to this repository. Sections 2–4 and 6 are shared
conventions for plasma-sds repositories developed with agents.

## 1. Project

`plasmasds_utility` is a package-agnostic utility that other plasma-sds packages (renate, neuro_bes, synref, …)
depend on to move data between the group's data server and a local working directory.

- **Spec of record:** [issue #6](https://github.com/plasma-sds/plasmasds_utility/issues/6), which contains the
  specification PDF and the discussion that amends it. Where the discussion and the PDF disagree, the discussion
  wins. The PDF is a direction, not a checklist: build feature set by feature set, as bare-bones as possible.
- **Board:** [plasma-sds project 10](https://github.com/orgs/plasma-sds/projects/10).
- **Targets:** Python 3.10+, Linux and Windows, published on PyPI.

Decisions already made (do not re-open them without an owner):

- Private data is fetched over SSH with paramiko; public data is downloaded with urllib. rsync is a possible future
  backend, not a current one.
- The utility owns the server base paths and the default local data directory. Each client owns its prefix (its
  package name) and the tree below it. A client may set its own working directory, which overrides the utility's
  default. The local tree mirrors the server tree.
- Packaged defaults are read-only. On first use they are copied as JSON into a per-user directory, and that copy is
  the only configuration file the utility writes. Nothing is ever written under `site-packages`.
- Data is downloaded only when it is missing locally. By default the server is not checked if non-dummy data is
  present; the first use in a session prints a notice saying so, together with the command that checks for new
  data. That check compares modification times and downloads when the server copy is newer. Explicit flags override
  the default in both directions. (Decided 2026-09-30.)
- Re-downloads triggered by a failed verification are bounded; they never loop.
- Failures are loud and specific: package-specific exception classes, messages that say where the failure happened,
  and a rotating log file.

### Commands

None yet. Setting up the package skeleton (pyproject, pixi environment, pytest, ruff, CI) is the next task; it will
add the test and lint commands here. "A passing test run" in section 2 means those commands.

## 2. Workflow

1. **Pick the work.** Take an item from the board, normally one in *Ready*. A plan is broken into sub-issues of its
   parent issue, one per step. Set the item to *In progress*.
2. **Branch off `development`.**
   `git fetch origin && git switch -c <issue>-<slug> --no-track origin/development` (for example `7-skeleton`;
   just `<slug>` when there is no issue).
   `--no-track` keeps a bare `git push` from targeting `development`.
3. **Implement one step**, in bite-sized commits (section 3). Stay inside the step; note anything else you find on
   the issue instead of fixing it.
4. **Open a draft pull request.** Push the branch and open the draft PR against `development` in the same step:
   `git push -u origin <branch> && gh pr create --draft --base development`. The body links the issue
   (`Closes #N`), names the plan step, and lists what was tested.
5. **Independent review.** A reviewer that did not write the change audits it against the lens in section 5. Post
   the findings and how each was resolved as a PR comment, then fix what needs fixing.
6. **Integrate.** If the branch changes a source, project or manifest file (`*.py`, `pyproject.toml`, `pixi.toml`,
   `pixi.lock`, CI workflows, packaged data), merge `origin/development` into the branch and run the full test suite
   on the merged state. Push, and state the result in the PR.
7. **Mark it ready.** `gh pr ready`, and set the board item to *In review*. Owners review and merge; agents never
   merge.
8. **Clean up after the merge.** Tag the step if it was a plan step (section 4). Bring the local `development` up
   to date with `git switch development && git pull --ff-only` (syncing is not working on it), then delete the
   branch with `git branch -d <branch>`. If `-d` still refuses, the branch is not merged: stop and find out why.

## 3. Git rules

- Never work on `master`. `development` reaches `master` only through an owner.
- Never commit on `development` directly, and never push it.
- The only things an agent pushes are the branch of its open pull request and tags.
- Keep commits bite-sized, so a human can read through them one at a time: one logical change per commit, with a
  message that says what and why. Keep moves, renames and formatting in their own commits, apart from changes in
  behaviour.
- Never squash, never force-push, never rebase a pushed branch. Bring `development` in by merging.
- Delete a merged branch with `git branch -d`, never `git branch -D`.
- Commits are signed. Every commit message ends with the `Co-Authored-By:` line of the model that wrote the change,
  and nothing after it.
- Agents act on GitHub as `leferi99-agent`. Before the first `gh` call of a session, `gh api user -q .login` must
  print `leferi99-agent`; if it does not, stop and ask.

## 4. Tags

- **Plan steps:** `step/<issue>-<n>` (for example `step/6-1`) on the merge commit in `development`, created after
  the owner has merged the step.
- **Releases:** `vMAJOR.MINOR.PATCH` ([semver](https://semver.org)) on `master`, after an owner has merged the
  release into `master`.
- Tags are annotated and signed: `git tag -s <tag> -m "<summary>" <commit>`, then `git push origin <tag>`.

## 5. Review lens

The review checks correctness first, then these four properties.

- **Performant**
  - A cache hit costs a `stat`, with no hashing and no server round-trip.
  - Transfers stream to disk; files are never held in memory whole.
  - Connections open lazily and are reused within a session.
- **Minimal**
  - Bare-bones: only what the current step needs, no speculative options or layers.
  - No new dependency without a stated reason. For now: paramiko plus the standard library.
  - Small public API; everything else is private.
- **Documented**
  - Every public function, class and module has a docstring with its parameters, return value and raised
    exceptions.
  - The README stays accurate for what is merged.
  - Decisions are recorded on the issue they affect.
- **Network-safe**
  - SSH host keys are verified; unknown hosts are rejected, never auto-accepted.
  - No secrets in code, packaged defaults, logs, exception messages, test fixtures or PR text.
  - Every connection and transfer has a timeout; retries are bounded with backoff.
  - Downloads go to a temporary file in the target directory and are moved into place atomically; a failed transfer
    leaves no partial file behind.
  - Resolved paths stay inside their configured base, locally and on the server; a crafted key or name cannot climb
    out with `..` or an absolute path.
  - Public downloads use HTTPS where the server offers it.

## 6. Tracking and communication

- **Board:** keep the item's status current (*Backlog → Ready → In progress → In review → Done*).
- **Plans:** a plan lives as sub-issues of its parent issue, one per step, in order, each small enough for one PR.
- **Discussions:** for design questions an owner has to decide (*Ideas* or *Q&A*), linked from the affected issue.
  Record the outcome on that issue, dated.
- **Language:** English, concise. This is a public repository; write accordingly.
