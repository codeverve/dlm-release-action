# Digital License Manager Release

A GitHub Action that publishes a release of your plugin, theme or app to a store running [Digital License Manager Pro](https://codeverve.com/product/digital-license-manager-pro/). Customers with a valid license see the update as soon as the job finishes, with no manual zip upload.

It uploads the package to the store's release API (`POST /wp-json/dlm/v1/software/{id}/releases`). For WordPress plugins and themes it also reads the requirements and changelog from the package.

## Usage

```yaml
name: Release

on:
  push:
    tags: ['v*']

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7

      # Build your package, e.g. build/my-plugin.zip
      - run: ./bin/build-zip.sh

      - uses: codeverve/dlm-release-action@v1
        with:
          url: https://example.com
          consumer-key: ${{ secrets.DLM_CONSUMER_KEY }}
          consumer-secret: ${{ secrets.DLM_CONSUMER_SECRET }}
          software-id: 12
          file: build/my-plugin.zip
```

Pushing `v1.4.0` publishes version 1.4.0 to the stable channel. Pushing `v1.5.0-rc.1` publishes 1.5.0-rc.1 to the RC channel, so only customers who opted into release candidates get it.

## Store setup

1. Run **Digital License Manager Pro 2.1.1** or newer on the store.
2. Find the **software ID** under *License Manager → Software*.
3. Create an API key under *License Manager → Settings → REST API*:
   - user: an administrator, or a user with the `dlm_edit_software` capability;
   - permissions: **Write** (or Read & Write);
   - routes: enable **034** (`POST v1/software/{software_id}/releases`). Enable **035** (the `GET` route) too if you call it.
4. Save the consumer key and secret as repository or organization secrets, e.g. `DLM_CONSUMER_KEY` and `DLM_CONSUMER_SECRET`.

Use a dedicated key for publishing. Never reuse the read-only key your plugin ships with for license checks. The store must be served over HTTPS: DLM refuses API keys over plain HTTP.

## Versions from tags

When `version` is empty, the version comes from the pushed tag:

| Tag | Version | Channel |
| --- | --- | --- |
| `v1.4.0` or `1.4.0` | 1.4.0 | stable |
| `my-plugin-pro-v1.4.0` | 1.4.0 | stable |
| `v1.5.0-rc.1` | 1.5.0-rc.1 | rc |
| `v1.5.0-beta.2` | 1.5.0-beta.2 | beta |
| `v1.5.0-alpha`, `v1.5.0-dev.3` | as tagged | alpha |

The `version` input accepts the same forms, so `${{ github.event.release.tag_name }}` works in a `release` workflow. Set `channel` to override the channel the suffix picks.

## What it reads from the package

For a ZIP package, any of these left empty is filled in automatically. Turn this off with `read-package: false`.

| Field | Read from |
| --- | --- |
| `tested-wp`, `requires-wp`, `requires-php` | `readme.txt` (`Tested up to`, `Requires at least`, `Requires PHP`), then the main plugin file's header or the theme's `style.css` |
| `changelog` | The version's entry under `== Changelog ==` in `readme.txt` |
| `upgrade-notice` | The version's entry under `== Upgrade Notice ==` in `readme.txt` |

A pre-release such as `1.5.0-rc.1` uses the `= 1.5.0 =` entry when it has none of its own.

WordPress software needs `tested-wp`, `requires-wp` and `requires-php`. The store refuses the release if any of them is missing from both the inputs and the package.

## Inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `url` | yes | | Store URL, e.g. `https://example.com`. |
| `consumer-key` | yes | | API consumer key. Keep it in a secret. |
| `consumer-secret` | yes | | API consumer secret. Keep it in a secret. |
| `software-id` | yes | | ID of the software item. |
| `file` | yes | | Path to the package, e.g. `build/my-plugin.zip`. |
| `version` | | the pushed tag | Version, or a tag name to read it from. |
| `channel` | | from the version | `stable`, `rc`, `beta` or `alpha`. |
| `changelog` | | from `readme.txt` | Release notes in Markdown. |
| `changelog-file` | | | File with the release notes, used when `changelog` is empty. The whole file is sent. |
| `tested-wp` | | from the package | WordPress version tested up to. |
| `requires-wp` | | from the package | Minimum WordPress version. |
| `requires-php` | | from the package | Minimum PHP version. |
| `upgrade-notice` | | from `readme.txt` | Notice shown on the WordPress update screen. |
| `read-package` | | `true` | Fill empty fields from the package. |
| `fields` | | | Extra release fields as `key=value` lines. |
| `make-current` | | `true` | Make the release the current one of its channel. |
| `notify` | | `false` | Queue the store's release email to customers. |

## Outputs

| Output | Description |
| --- | --- |
| `release-id` | ID of the release on the store. |
| `version` | Published version. |
| `channel` | Release channel. |
| `created` | `true` for a new release, `false` when this version was already published with the same file. |
| `current` | Whether the release is the current one of its channel. |
| `sha256` | SHA-256 of the published file. |

## Re-runs and failures

Publishing is idempotent per version. Re-running a job with the same file succeeds without changing anything (`created: false`). Publishing an existing version with a different file fails with `409 release_exists`: publish a new version instead.

The action sends the package's SHA-256, so a corrupted upload is refused. It retries network errors and 408, 429, 502, 503 and 504 responses up to three times. Any other error fails the step with the store's message and a hint.

## Running it elsewhere

The action is a Bash script that needs `curl`, `jq` and `unzip`, all present on GitHub's Linux and macOS runners. Outside GitHub Actions, set the same values as `DLM_*` environment variables and run `publish.sh`:

```bash
DLM_URL=https://example.com DLM_CONSUMER_KEY=ck_… DLM_CONSUMER_SECRET=cs_… \
DLM_SOFTWARE_ID=12 DLM_FILE=build/my-plugin.zip DLM_VERSION=1.4.0 ./publish.sh
```

## License

MIT
