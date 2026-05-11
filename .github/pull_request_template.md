<!--
Thanks for the PR. Fill in the sections below; remove the ones that don't apply.
Keep the description short and focused — the diff itself tells most of the story.
-->

## Summary

<!-- 1-3 sentences: what does this PR do, and why? -->

## Changes

<!-- Bullet list of actual changes. Mention files when it helps. -->

-
-

## Test plan

<!-- How was this verified? Tick what applies; remove items that don't. -->

- [ ] Manual run against a live Hermes Gateway (`launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway` or your equivalent restart command).
- [ ] `~/.hermes/logs/agent.log` shows no new errors after restart.
- [ ] If `_handle_post` was touched, hedged-request behavior was verified (both parallel POSTs return identical content; placeholder does not race ahead of the real reply).
- [ ] If `README.md` / `README.ja.md` were touched, both are updated in this PR (dual-language sync).
- [ ] If `plugin.yaml` `version` was bumped, `CHANGELOG.md` `[Unreleased]` was promoted to the new version section.

## Related

<!-- `Closes #N` auto-closes the issue on merge. `Refs #N` links without closing. -->

Closes #
