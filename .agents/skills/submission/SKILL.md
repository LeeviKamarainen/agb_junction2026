---
name: submission
description: Draft the Junction 2026 project submission, including the disclosure of work that existed before the event.
disable-model-invocation: true
---

# Junction submission

Junction requires projects to be built mainly during the event, with any pre-existing code, datasets or frameworks clearly identified in the submission. Everything committed before the event is the `pre-event-scaffold` git tag, summarised in the README section "Built before the event".

1. Get the submission form's fields and the challenge brief from the user.
2. Gather the material: `README.md`, `git log --oneline pre-event-scaffold..HEAD`, `git diff --stat pre-event-scaffold..HEAD`, and the live URL from the latest deploy summary (`gh run view`).
3. Draft text for every field: the problem, what the project does, how it works, what was built during the event, and how to try it (live URL and demo steps).
4. Write the disclosure: the pre-event scaffold (link the README section and the tag), plus every third-party API, model, dataset and notable library the project depends on.
5. Check access: whether judges need the repository public or shared (ask the user), and that git history contains no secrets.

Done when every form field has text and the disclosure names everything the team did not build during the event.
