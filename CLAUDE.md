@AGENTS.md

## Claude Code specifics

These add to AGENTS.md; they do not replace any of it.

### Worktrees

- Work in a worktree, never in the main checkout. Create the branch and the worktree from `origin/development`,
  then enter it:
  `git fetch origin && git worktree add -b <branch> --no-track .claude/worktrees/<branch> origin/development`,
  followed by `EnterWorktree` with that `path`. (`EnterWorktree` with a `name` branches from `origin/master` under
  the default `worktree.baseRef`; do not use it.)
- Leaving: use `ExitWorktree` with *keep*, or choose *keep* at the exit prompt. *Remove* may force the removal.
- Removing a worktree, after its PR is merged, from a session in the main checkout (git commands from inside a
  worktree session cannot reach it):
  1. List the links in it: `find .claude/worktrees/<branch> -path '*/.git' -prune -o -type l -print`.
  2. `unlink` each link you or the worktree tool created.
  3. `git worktree remove .claude/worktrees/<branch>`.
  4. `git branch -d <branch>`.
- Never `--force`, and never `rm -r` on a link. If `git worktree remove` refuses, find out why instead of forcing it.

### Independent review

The independent review in AGENTS.md step 5 is the `advisor` tool, asked to audit the change against the review
lens. Post the result as a PR comment headed **Advisor review**, with each finding and how it was resolved.

### Commits and pull requests

- End every commit message with the `Co-Authored-By:` line for the model in use, as given by the harness. Do not
  add a `Claude-Session:` line or any other trailer.
- End pull request descriptions with the Claude Code attribution line given by the harness.
