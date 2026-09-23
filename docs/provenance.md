# Source and toolchain provenance

## Selection

Issues #1–#4 are implemented together. The Rails baseline and fixed tools are prerequisites; strict transpilation and native startup expose the highest compatibility risk early.

| Issue | Importance | Difficulty | Order |
|---|---|---|---|
| #1 Rails baseline | High: reference behavior for every later comparison | Medium | First |
| #2 Toolchain | High: all generation and build results must be repeatable | Medium | Alongside #1 |
| #3 Transpilation | High: determines whether this Rails source is supported | High | After #1/#2 |
| #4 Native binary | High: proves the AOT path actually runs | High | After #3 |

## Generated application

`blog/` was generated on GitHub Actions from Rails 8.1.3.1 using Ruby 3.4.5 and Bundler 2.6.9. The exact resolved gems are in `blog/Gemfile.lock`. The generator is the unchanged `scripts/create-blog` from Roundhouse v2026.9.18 (Git blob `3c89d6dc94340bbbb3a31d7957803c3c704fcf34`), vendored with its MIT license.

The wrapper removes newly generated credentials, fixes the Rails dependency to exactly 8.1.3.1, and prepares the schema/seeds. The checked-in schema and migrations came from the generator. Gem lockfiles are committed; ordinary CI runs do not regenerate application source.

## Native tools

Roundhouse v2026.9.18 and Spinel 2026.09.12 are the pairing documented in [Roundhouse RELEASES.md](https://github.com/rubys/roundhouse/blob/v2026.9.18/RELEASES.md). Release archives are checked against the SHA-256 values in `config/toolchain.env`. This pins the native tools; OS packages are resolved from Ubuntu 24.04 repositories, so the build is reproducible at the source/version level, not promised byte-for-byte.

## Assets

The checked-in Rails bundle builds Tailwind CSS and provides Turbo/Stimulus. `scripts/prepare-assets.sh` stages those files and application JavaScript. Roundhouse's documented `ROUNDHOUSE_ASSETS_DIR` hook includes them in the emitted project. No generated Ruby is patched by hand.

## Validation

The initial bootstrap [Actions run](https://github.com/koduki/example-rails-aot/actions/runs/35874563878) passed:

- Rails: 21 tests, 54 assertions, zero failures/errors/skips.
- Strict Roundhouse analysis and emission, followed by `spin build`.
- Native HTTP index, invalid input (422), article creation/redirect, detail read, and SQLite persistence after restart.
- The same runtime package in an Ubuntu 24.04 container without Ruby or Spinel.

That initial run preceded committed-source and asset packaging changes. The latest PR check is the authority for the current tree. Diagnostics and runtime artifacts are attached to each run.

Roundhouse's analyzer reports 0 parse errors, 0 errors, 0 warnings and 0 survey gaps. The separate lowering/emission stage reports four `lower_residue` warnings:

| Source | Diagnostic and impact |
|---|---|
| `articles_controller.rb:32,45` | The inline `render json: @article.errors` arm is dropped because the encoder is unavailable. The diagnostic says JSON negotiation for create/update falls back to HTML. These JSON paths are not claimed equivalent. |
| `application_mailer.rb:2,3` | `default` and `layout` are not modeled. The generated base mailer has no mail actions and is unused by the blog workflows. Mail behavior is not validated. |

The code is emitted in strict mode without survey/stub flags; strict success is not proof of full semantic equivalence. Preserve these warnings and cover the JSON cases explicitly in #5/#6 before declaring parity. No application feature has been deleted to hide these warnings.

Spinel also reports two upstream RBS boxed-array warnings (`Attached.@variations`, `UserAgent.@tokens`). They do not prevent this build or the exercised operations; warnings remain in the build log. They are not evidence that all framework paths are validated. Rails/AOT differential testing, Cable behavior and runtime-image distribution remain in #5–#9.
