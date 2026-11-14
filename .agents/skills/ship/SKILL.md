---
name: ship
description: Deploy to production and confirm it works, or diagnose a failed deploy. Use when shipping changes, when a deploy fails, or when production misbehaves.
---

# Ship

Shipping lands a pull request on `main`, which deploys it to production. A person on the team must explicitly approve it before step 2.

1. Confirm the pull request is ready: `gh pr checks <number>` is green.
2. Merge it with `gh pr merge <number> --squash --delete-branch`. To put an unmerged branch on production instead, run `gh workflow run ci-cd.yml --ref <branch>`.
3. Follow the deploy with `gh run watch`. The deploy job's summary shows the site URL.
4. If the run went red, branch on the failed step:
   - **checks**: reproduce with `make check`, then fix it in a new pull request.
   - **Write the production .env files**: a GitHub secret or variable named `APP_*` is malformed or duplicated; the log names it.
   - **Build and start the containers**: the Docker build failed on the VM and the previous version is still serving; the error is in the job log.
   - **Wait for the health check**: the new containers started but the site is not healthy; read `make prod-logs`.
5. Open the site URL and use the feature you shipped.

Done when the deploy job is green and the shipped feature works on the live site. To undo a bad deploy, ship a pull request that `git revert`s the commit.
