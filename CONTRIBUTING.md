# Contributing to hermes-even-ai-plugin

Thanks for considering a contribution. This guide covers everything you need to develop and submit changes against this plugin.

For end-user setup, see the [README](./README.md). The Japanese version of this guide is [CONTRIBUTING.ja.md](./CONTRIBUTING.ja.md).

## Scope

This plugin is a **Hermes Agent platform adapter** that bridges the Even App's "Add Agent" feature to a Hermes session. In scope:

- Fixes and enhancements to the inbound HTTPS handler, hedged-request handling, Telegram fallback path, and G2-aware formatting.
- Compatibility with new Hermes Agent versions and new Even App versions.
- Documentation improvements (English / Japanese).

Out of scope (please open issues upstream):

- Changes to Hermes Agent core itself → [Hermes Agent](https://github.com/NousResearch/hermes-agent).
- Changes to the Even App or G2 firmware → vendor's support channels.

## Development setup

### 1. Clone and symlink into Hermes

```bash
git clone https://github.com/kisaragi-mochi/hermes-even-ai-plugin.git \
  ~/src/hermes-even-ai-plugin
ln -s ~/src/hermes-even-ai-plugin ~/.hermes/plugins/even-ai
```

The symlink lets you edit the working tree and reload changes in Hermes via a Gateway restart, without re-copying files.

### 2. Enable the plugin

```bash
hermes plugins enable even-ai-platform
```

User plugins are opt-in (bundled plugins auto-load; user plugins must be enabled explicitly).

### 3. Configure environment

Set the env vars in `~/.hermes/.env` (see [README — Configuration](./README.md#configuration) for the full list). At minimum for development:

```bash
EVEN_AI_AUTH_TOKEN=<a long random string>
EVEN_AI_ALLOW_ALL_USERS=true
EVEN_AI_BIND_PORT=8767
EVEN_AI_RESPONSE_TIMEOUT=28
EVEN_AI_TELEGRAM_FALLBACK=true
```

### 4. Restart the Gateway and verify

Plugin code is loaded once per Hermes process. After editing, **restart the Gateway**:

```bash
# macOS launchd
launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway

# Or your equivalent restart command
hermes restart
```

Then watch `~/.hermes/logs/agent.log`:

```bash
tail -F ~/.hermes/logs/agent.log
```

Look for `connecting to even-ai... ✓ even-ai connected` and the absence of errors after restart.

### 5. Smoke-test on a real G2 (optional, recommended)

If you have access to G2 + the Even App, speak a short prompt and confirm the HUD displays the agent reply. If you don't have hardware, the maintainer can help verify before merge — say so in the PR description.

## Making changes

### Branch naming

Use `issue-<N>-<short-desc>`, e.g. `issue-12-fix-401-whitespace`. This makes the cross-reference between the branch, the originating issue, and the merge commit obvious.

### Commit style

Conventional commits ([reference](https://www.conventionalcommits.org/)):

- `feat: ...` — new behavior / new env var / new endpoint.
- `fix: ...` — bug fix.
- `docs: ...` — README / CHANGELOG / CONTRIBUTING etc.
- `chore: ...` — housekeeping (dependencies, formatting).

Keep commits focused: one logical change per commit.

### EN / JP README dual-language sync

This repository ships parallel English and Japanese READMEs. **If you change one, change the other in the same commit.** Self-check before push:

```bash
git diff main..HEAD --stat | grep -E '(README\.md|README\.ja\.md)'
```

Both should appear, or neither.

### Don't commit personal config

Verify `git diff main..HEAD --stat` does not include `.env` or any file with personal hostnames / tokens before push.

## Submitting a pull request

1. **Open or pick up an issue first.** For non-trivial changes, opening an issue (or commenting on one) makes the discussion happen before code is written.
2. **Push your branch** and open a PR. GitHub auto-fills the description from the repository's pull request template — just keep the structure it gives you and fill in the blanks.
3. **Link the issue** with `Closes #N` so it closes on merge.
4. **Fill out the Test plan checklist** in the PR body. Tick what you verified; remove items that don't apply.
5. **One reviewer is enough.** The maintainer merges once the checklist and diff look correct. CI is not currently set up.

## Reporting bugs / requesting features

Use the issue templates:

- [Bug report (English)](https://github.com/kisaragi-mochi/hermes-even-ai-plugin/issues/new?template=bug_report.yml)
- [Bug report (日本語)](https://github.com/kisaragi-mochi/hermes-even-ai-plugin/issues/new?template=bug_report.ja.yml)
- [Feature request (English)](https://github.com/kisaragi-mochi/hermes-even-ai-plugin/issues/new?template=feature_request.yml)
- [Feature request (日本語)](https://github.com/kisaragi-mochi/hermes-even-ai-plugin/issues/new?template=feature_request.ja.yml)

The bug-report template asks for Hermes / Python / plugin / Even App versions, host OS, and the HTTPS front-end in use — please fill these in. Symptoms differ a lot by combination.

## Code of Conduct

Participation in this project is governed by the [Contributor Covenant Code of Conduct](./CODE_OF_CONDUCT.md). By contributing, you agree to abide by its terms.

## License

By contributing to this repository, you agree that your contributions will be licensed under the [MIT License](./LICENSE).
