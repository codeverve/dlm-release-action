#!/usr/bin/env bash
# Publish a release to a Digital License Manager Pro store:
# POST {url}/wp-json/dlm/v1/software/{software-id}/releases (multipart, the package in "file").
#
# Configured through the DLM_* environment variables that action.yml sets from its inputs, so it
# also runs outside GitHub Actions, e.g. DLM_URL=… DLM_SOFTWARE_ID=12 DLM_FILE=x.zip ./publish.sh
set -euo pipefail

SEMVER='[0-9]+(\.[0-9]+){0,3}(-[0-9A-Za-z.-]+)?'
RESERVED_FIELDS=' file version channel changelog sha256 make_current notify software_id '

error() {
	echo "::error title=DLM release::$*"
	exit 1
}

notice() {
	echo "::notice title=DLM release::$*"
}

output() {
	if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
		printf '%s=%s\n' "$1" "$2" >> "$GITHUB_OUTPUT"
	fi
	printf '  %-11s %s\n' "$1:" "$2"
}

# Normalise a boolean input into the variable named $1: bool make_current "$DLM_MAKE_CURRENT" make-current
bool() {
	case "$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')" in
		true | 1 | yes) printf -v "$1" true ;;
		false | 0 | no | '') printf -v "$1" false ;;
		*) error "$3 must be true or false, got '$2'." ;;
	esac
}

# Quote a value for curl's -F syntax (name=@"path";filename="name").
curl_quote() {
	local value=${1//\\/\\\\}
	printf '"%s"' "${value//\"/\\\"}"
}

# First value of a "Header: value" line in a readme.txt, plugin header or style.css.
header() {
	local name=$1 file=$2
	[[ -f "$file" ]] || return 0
	grep -m1 -iE "^[[:space:]/*#@]*${name}:" "$file" \
		| sed -E 's/^[^:]*:[[:space:]]*//; s/\r$//; s/[[:space:]]*\*\/.*$//; s/[[:space:]]+$//' || true
}

# The entry for a version in a readme.txt section ("Changelog", "Upgrade Notice"). A pre-release
# such as 1.2.0-rc.1 falls back to the 1.2.0 entry, since version markers carry the plain version.
readme_entry() {
	local file=$1 section=$2 version=$3 entry
	[[ -f "$file" ]] || return 0
	entry=$(readme_section_entry "$file" "$section" "$version")
	if [[ -z "$entry" && "$version" == *-* ]]; then
		entry=$(readme_section_entry "$file" "$section" "${version%%-*}")
	fi
	printf '%s' "$entry"
}

readme_section_entry() {
	awk -v section="$2" -v want="$3" '
		{ sub(/\r$/, "") }
		/^===/ { next }
		/^==/ { if (grab) exit; insection = (index(tolower($0), section) > 0); next }
		/^=[^=]/ {
			if (grab) exit
			if (!insection) next
			heading = $0
			gsub(/^=[[:space:]]*|[[:space:]]*=[[:space:]]*$/, "", heading)
			sub(/^[vV]/, "", heading)
			rest = substr(heading, length(want) + 1)
			if (substr(heading, 1, length(want)) == want && (rest == "" || rest ~ /^[[:space:](]/)) grab = 1
			next
		}
		grab { lines[++count] = $0 }
		END {
			first = 1; last = count
			while (first <= last && lines[first] ~ /^[[:space:]]*$/) first++
			while (last >= first && lines[last] ~ /^[[:space:]]*$/) last--
			for (i = first; i <= last; i++) print lines[i]
		}' "$1"
}

# --- Inputs ---------------------------------------------------------------------------------------
url=${DLM_URL:-}
key=${DLM_CONSUMER_KEY:-}
secret=${DLM_CONSUMER_SECRET:-}
software_id=${DLM_SOFTWARE_ID:-}
file=${DLM_FILE:-}

[[ -n "$url" ]] || error "Set the url input to your store, e.g. https://example.com."
[[ -n "$key" && -n "$secret" ]] || error "Set consumer-key and consumer-secret, from secrets."
if [[ "${GITHUB_ACTIONS:-}" == true ]]; then
	echo "::add-mask::$key"
	echo "::add-mask::$secret"
fi
[[ "$software_id" =~ ^[1-9][0-9]*$ ]] || error "software-id must be the numeric ID of the software item, got '$software_id'."
[[ -f "$file" && -s "$file" ]] || error "The release file '$file' does not exist or is empty."

url=${url%/}
case "$url" in
	*/wp-json/dlm/v1) base=$url ;;
	*/wp-json) base="$url/dlm/v1" ;;
	https://* | http://*) base="$url/wp-json/dlm/v1" ;;
	*) error "url must start with https://, got '$url'." ;;
esac
[[ "$base" == https://* ]] || echo "::warning title=DLM release::$url is not HTTPS; DLM refuses API keys over plain HTTP unless the store disables that check."
endpoint="$base/software/$software_id/releases"

# The version input or the pushed tag: 1.2.0, v1.2.0 and my-plugin-pro-v1.2.0 all give 1.2.0.
tag=${DLM_VERSION:-}
if [[ -z "$tag" ]]; then
	[[ "${GITHUB_REF_TYPE:-}" == tag ]] || error "Set the version input: this run was not started by a tag."
	tag=$GITHUB_REF_NAME
fi
if [[ "$tag" =~ ^v?(${SEMVER})$ ]] || [[ "$tag" =~ -v(${SEMVER})$ ]]; then
	version=${BASH_REMATCH[1]}
elif [[ -n "${DLM_VERSION:-}" ]]; then
	error "The version must look like 1.2.3, 1.2.3-rc.1 or my-plugin-v1.2.3, got '$tag'."
else
	error "Could not read a version from the tag '$tag'. Use tags like v1.2.0 or my-plugin-pro-v1.2.0, or set the version input."
fi

channel=${DLM_CHANNEL:-}
case "$channel" in
	'' | stable | rc | beta | alpha) ;;
	*) error "channel must be stable, rc, beta or alpha, got '$channel'." ;;
esac

make_current=true notify=false read_package=true
bool make_current "${DLM_MAKE_CURRENT:-true}" make-current
bool notify "${DLM_NOTIFY:-false}" notify
bool read_package "${DLM_READ_PACKAGE:-true}" read-package

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

if command -v sha256sum > /dev/null; then
	sha256=$(sha256sum "$file" | cut -d' ' -f1)
else
	sha256=$(shasum -a 256 "$file" | cut -d' ' -f1)
fi

# --- Release fields, from the inputs or the package -------------------------------------------------
changelog=${DLM_CHANGELOG:-}
if [[ -z "$changelog" && -n "${DLM_CHANGELOG_FILE:-}" ]]; then
	[[ -f "$DLM_CHANGELOG_FILE" ]] || error "changelog-file '$DLM_CHANGELOG_FILE' does not exist."
	changelog=$(< "$DLM_CHANGELOG_FILE")
fi
tested_wp=${DLM_TESTED_WP:-}
requires_wp=${DLM_REQUIRES_WP:-}
requires_php=${DLM_REQUIRES_PHP:-}
upgrade_notice=${DLM_UPGRADE_NOTICE:-}
sources=()

if [[ "$read_package" == true && "$(head -c 2 "$file")" == PK ]]; then
	entries=$(unzip -Z1 "$file" 2> /dev/null || true)
	readme_entry_name=$(grep -m1 -iE '^([^/]+/)?readme\.txt$' <<< "$entries" || true)
	if [[ -n "$readme_entry_name" ]]; then
		unzip -p "$file" "$readme_entry_name" > "$work/readme.txt" 2> /dev/null || true
	fi
	# The main plugin file (a "Plugin Name:" header) or a theme's style.css ("Theme Name:").
	while IFS= read -r entry; do
		[[ -n "$entry" ]] || continue
		unzip -p "$file" "$entry" > "$work/candidate" 2> /dev/null || continue
		head -c 8192 "$work/candidate" > "$work/candidate.head"
		if grep -qiE '^[[:space:]/*#@]*(Plugin|Theme) Name:' "$work/candidate.head"; then
			mv "$work/candidate.head" "$work/header"
			header_entry_name=$entry
			break
		fi
	done < <(grep -E '^([^/]+/)?([^/]+\.php|style\.css)$' <<< "$entries" | head -n 50 || true)

	for pair in 'tested_wp|Tested up to' 'requires_wp|Requires at least' 'requires_php|Requires PHP'; do
		name=${pair%%|*} label=${pair#*|}
		[[ -z "${!name}" ]] || continue
		value=$(header "$label" "$work/readme.txt")
		from=$readme_entry_name
		if [[ -z "$value" ]]; then
			value=$(header "$label" "$work/header")
			from=${header_entry_name:-}
		fi
		if [[ -n "$value" ]]; then
			printf -v "$name" '%s' "$value"
			sources+=("$name from $from")
		fi
	done
	if [[ -z "$changelog" ]]; then
		changelog=$(readme_entry "$work/readme.txt" changelog "$version")
		[[ -z "$changelog" ]] || sources+=("changelog from $readme_entry_name")
	fi
	if [[ -z "$upgrade_notice" ]]; then
		upgrade_notice=$(readme_entry "$work/readme.txt" 'upgrade notice' "$version")
		[[ -z "$upgrade_notice" ]] || sources+=("upgrade_notice from $readme_entry_name")
	fi
fi

form=(-F "file=@$(curl_quote "$file");filename=$(curl_quote "$(basename "$file")")")
form+=(--form-string "version=$version" --form-string "sha256=$sha256")
form+=(--form-string "make_current=$make_current" --form-string "notify=$notify")
[[ -z "$channel" ]] || form+=(--form-string "channel=$channel")
[[ -z "$changelog" ]] || form+=(--form-string "changelog=$changelog")
[[ -z "$tested_wp" ]] || form+=(--form-string "tested_wp=$tested_wp")
[[ -z "$requires_wp" ]] || form+=(--form-string "requires_wp=$requires_wp")
[[ -z "$requires_php" ]] || form+=(--form-string "requires_php=$requires_php")
[[ -z "$upgrade_notice" ]] || form+=(--form-string "upgrade_notice=$upgrade_notice")

while IFS= read -r line; do
	line=${line%$'\r'}
	[[ -n "${line//[[:space:]]/}" ]] || continue
	[[ "$line" == *=* ]] || error "fields lines must be key=value, got '$line'."
	field=${line%%=*}
	field=${field//[[:space:]]/}
	[[ "$field" =~ ^[A-Za-z0-9_]+$ ]] || error "Invalid field name '$field' in fields."
	[[ "$RESERVED_FIELDS" != *" $field "* ]] || error "Set '$field' with its own input, not in fields."
	form+=(--form-string "$field=${line#*=}")
done <<< "${DLM_FIELDS:-}"

# --- Publish -----------------------------------------------------------------------------------------
echo "Publishing $(basename "$file") ($(wc -c < "$file" | tr -d ' ') bytes, sha256 $sha256)"
echo "  to:      $endpoint"
echo "  version: $version${channel:+ (channel $channel)}"
if ((${#sources[@]})); then
	printf '  read %s\n' "${sources[@]}"
fi

# Credentials go through a config file, never the command line.
umask 077
printf 'user = %s\n' "$(curl_quote "$key:$secret")" > "$work/auth.conf"

# Retry network errors and temporary unavailability only. Retrying is safe, since publishing the
# same version with the same file is idempotent; other errors, 500 included, are reported as they are.
attempt=1
while :; do
	status=$(curl --silent --show-error --connect-timeout 30 --max-time 900 \
		--config "$work/auth.conf" \
		-H 'Accept: application/json' -A 'dlm-release-action/1' \
		-D "$work/headers" -o "$work/response" -w '%{http_code}' \
		"${form[@]}" "$endpoint") && curl_exit=0 || curl_exit=$?
	if ((curl_exit == 0)) && [[ ! "$status" =~ ^(408|429|502|503|504)$ ]]; then
		break
	fi
	if ((attempt >= ${DLM_ATTEMPTS:-3})); then
		((curl_exit == 0)) || error "Could not reach $endpoint (curl exit code $curl_exit)."
		break
	fi
	echo "Attempt $attempt failed ($( ((curl_exit)) && echo "curl exit code $curl_exit" || echo "HTTP $status")); retrying in $((attempt * ${DLM_RETRY_DELAY:-5}))s."
	sleep $((attempt * ${DLM_RETRY_DELAY:-5}))
	attempt=$((attempt + 1))
done

# A response value as text; false stays "false", null and missing are empty.
json() {
	jq -r "$1 | if . == null then empty else tostring end" "$work/response" 2> /dev/null || true
}

# The response as one line of text, e.g. a PHP error page without its HTML.
body_text() {
	sed -e 's/<[^>]*>//g' "$work/response" | tr -s '[:space:]' ' ' | head -c 400
}

case "$status" in
	200 | 201)
		[[ "$(json '.success')" == true ]] || error "HTTP $status with an unexpected response: $(body_text) If the store runs DLM Pro 2.1.0, update it to 2.1.1 or newer."
		created=$(json '.data.created')
		echo
		output release-id "$(json '.data.id')"
		output version "$(json '.data.version')"
		output channel "$(json '.data.channel')"
		output created "$created"
		output current "$(json '.data.current')"
		output sha256 "$(json '.data.sha256')"
		notification=$(json '.data.notification')
		[[ -z "$notification" ]] || echo "  notification: $notification"
		if [[ "$created" == true ]]; then
			notice "Published $version as release $(json '.data.id') ($(json '.data.channel'))."
		else
			notice "$version was already published with this file (release $(json '.data.id')); nothing changed."
		fi
		if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
			{
				echo "### DLM release $version"
				echo
				echo "| Release | Channel | New | Current | SHA-256 |"
				echo "| --- | --- | --- | --- | --- |"
				echo "| $(json '.data.id') | $(json '.data.channel') | $created | $(json '.data.current') | \`${sha256:0:12}\` |"
			} >> "$GITHUB_STEP_SUMMARY"
		fi
		;;
	3??)
		location=$(grep -i '^location:' "$work/headers" | tail -n1 | cut -d' ' -f2- | tr -d '\r')
		error "The store redirected to ${location:-another URL} ($status). Set url to the final address."
		;;
	*)
		code=$(json '.code')
		message=$(json '.message')
		[[ -n "$code$message" ]] || message=$(body_text)
		hint=''
		case "$status:$code" in
			*:rest_no_route) hint='This store has no release API: it needs Digital License Manager Pro 2.1.1 or newer.' ;;
			*:rest_no_ssl_error) hint='Use the https:// URL of the store.' ;;
			*:dlm_rest_route_disabled_error) hint='Enable route 034 (POST v1/software/{software_id}/releases) on the API key.' ;;
			*:permission_denied) hint="The key's user needs the dlm_edit_software capability (administrators have it)." ;;
			*:software_not_found) hint="There is no software with ID $software_id on this store." ;;
			*:release_exists) hint="$version is already published with a different file. Publish a new version instead." ;;
			*:checksum_mismatch) hint='The upload was corrupted on the way; run the job again.' ;;
			401:*) hint='Check consumer-key and consumer-secret, and that the key has Write or Read & Write permission.' ;;
			500:) hint='The store had a server error; its PHP error log has the details. DLM Pro 2.1.0 fails here: update the store to 2.1.1 or newer.' ;;
			413:*) hint="The package is larger than the server accepts: raise PHP's upload_max_filesize and post_max_size, and the web server's body size limit." ;;
		esac
		error "HTTP $status${code:+ $code}: ${message:-no message}${hint:+ $hint}"
		;;
esac
