"""Tests for publish.sh against a mock Digital License Manager store.

Run: python3 -m unittest discover -s tests -v
"""

import base64
import hashlib
import os
import subprocess
import tempfile
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

from mock_store import Store, created_response, error_response

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "publish.sh"
KEY, SECRET = "ck_test", "cs_secret"

README = """=== Example Plugin ===
Requires at least: 6.6
Tested up to: 7.1
Requires PHP: 8.1
Stable tag: 1.3.0

== Description ==

Example.

== Changelog ==

= 1.3.0 - 2026-10-14 =
* New: the thing.
* Fix: the other thing.

= 1.2.0 =
* Older entry.

== Upgrade Notice ==

= 1.3.0 =
Update for the thing.
"""

PLUGIN_HEADER = """<?php
/**
 * Plugin Name: Example Plugin Pro
 * Version: 2.0.0
 * Requires at least: 6.5
 * Tested up to: 7.0
 * Requires PHP: 7.4
 */
"""

THEME_STYLE = """/*
Theme Name: Example Theme
Requires at least: 6.4
Tested up to: 6.9
Requires PHP: 8.0
*/
"""


class PublishTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Store)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        Store.requests.clear()
        Store.responses.clear()
        self.tmp = Path(tempfile.mkdtemp())

    def package(self, name="example-plugin.zip", **files):
        path = self.tmp / name
        with zipfile.ZipFile(path, "w") as archive:
            for entry, contents in files.items():
                archive.writestr(entry.replace("__", "/"), contents)
        return path

    def plugin_zip(self, name="example-plugin.zip"):
        return self.package(
            name,
            **{"example-plugin__readme.txt": README, "example-plugin__example-plugin.php": PLUGIN_HEADER},
        )

    def run_action(self, file, **env):
        output, summary = self.tmp / "output", self.tmp / "summary"
        output.write_text("")
        summary.write_text("")
        base = {
            "PATH": os.environ["PATH"],
            "GITHUB_ACTIONS": "true",
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
            "DLM_URL": self.url,
            "DLM_CONSUMER_KEY": KEY,
            "DLM_CONSUMER_SECRET": SECRET,
            "DLM_SOFTWARE_ID": "12",
            "DLM_FILE": str(file),
            "DLM_RETRY_DELAY": "0",
        }
        base.update({key: str(value) for key, value in env.items()})
        result = subprocess.run(["bash", str(SCRIPT)], env=base, capture_output=True, text=True, timeout=60)
        result.outputs = dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
        result.summary = summary.read_text()
        return result

    def assertPublished(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assertFailed(self, result, *needles):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        for needle in needles:
            self.assertIn(needle, result.stdout)

    def test_publishes_a_plugin_from_a_tag_with_its_readme(self):
        file = self.plugin_zip()
        result = self.run_action(file, GITHUB_REF_TYPE="tag", GITHUB_REF_NAME="v1.3.0")

        self.assertPublished(result)
        request = Store.requests[0]
        self.assertEqual(request["path"], "/wp-json/dlm/v1/software/12/releases")
        self.assertEqual(request["auth"], "Basic " + base64.b64encode(f"{KEY}:{SECRET}".encode()).decode())
        fields = request["fields"]
        self.assertEqual(fields["version"], "1.3.0")
        self.assertEqual(fields["sha256"], hashlib.sha256(file.read_bytes()).hexdigest())
        self.assertEqual((fields["tested_wp"], fields["requires_wp"], fields["requires_php"]), ("7.1", "6.6", "8.1"))
        self.assertEqual(fields["changelog"], "* New: the thing.\n* Fix: the other thing.")
        self.assertEqual(fields["upgrade_notice"], "Update for the thing.")
        self.assertEqual((fields["make_current"], fields["notify"]), ("true", "false"))
        self.assertNotIn("channel", fields, "the store derives the channel from the version")
        self.assertEqual(request["files"]["file"]["filename"], "example-plugin.zip")
        self.assertEqual(request["files"]["file"]["data"], file.read_bytes())

        self.assertEqual(result.outputs["release-id"], "42")
        self.assertEqual(result.outputs["created"], "true")
        self.assertEqual(result.outputs["current"], "true")
        self.assertIn("### DLM release 1.3.0", result.summary)
        self.assertIn("::add-mask::cs_secret", result.stdout)
        self.assertNotIn(SECRET, result.stdout.replace("::add-mask::cs_secret", ""))

    def test_reads_the_version_from_a_prefixed_pre_release_tag(self):
        result = self.run_action(
            self.plugin_zip(), GITHUB_REF_TYPE="tag", GITHUB_REF_NAME="example-plugin-pro-v1.3.0-rc.1"
        )

        self.assertPublished(result)
        fields = Store.requests[0]["fields"]
        self.assertEqual(fields["version"], "1.3.0-rc.1")
        self.assertEqual(fields["changelog"], "* New: the thing.\n* Fix: the other thing.", "falls back to the 1.3.0 entry")

    def test_falls_back_to_the_plugin_header(self):
        file = self.package(**{"example-plugin-pro__example-plugin-pro.php": PLUGIN_HEADER, "example-plugin-pro__inc__a.php": "<?php"})
        result = self.run_action(file, DLM_VERSION="2.0.0")

        self.assertPublished(result)
        fields = Store.requests[0]["fields"]
        self.assertEqual((fields["tested_wp"], fields["requires_wp"], fields["requires_php"]), ("7.0", "6.5", "7.4"))
        self.assertNotIn("changelog", fields)

    def test_reads_a_theme_style_sheet(self):
        file = self.package("example-theme.zip", **{"example-theme__style.css": THEME_STYLE, "example-theme__functions.php": "<?php"})
        result = self.run_action(file, DLM_VERSION="1.0.0")

        self.assertPublished(result)
        fields = Store.requests[0]["fields"]
        self.assertEqual((fields["tested_wp"], fields["requires_wp"], fields["requires_php"]), ("6.9", "6.4", "8.0"))

    def test_inputs_win_over_the_package(self):
        changelog = self.tmp / "notes.md"
        changelog.write_text("@not-a-file <not-a-file either\n")
        result = self.run_action(
            self.plugin_zip(),
            DLM_VERSION="v1.3.0",
            DLM_CHANNEL="beta",
            DLM_TESTED_WP="7.2",
            DLM_CHANGELOG_FILE=changelog,
            DLM_MAKE_CURRENT="false",
            DLM_NOTIFY="yes",
        )

        self.assertPublished(result)
        fields = Store.requests[0]["fields"]
        self.assertEqual(fields["version"], "1.3.0")
        self.assertEqual(fields["channel"], "beta")
        self.assertEqual(fields["tested_wp"], "7.2")
        self.assertEqual(fields["requires_wp"], "6.6")
        self.assertEqual(fields["changelog"], "@not-a-file <not-a-file either", "sent literally")
        self.assertEqual((fields["make_current"], fields["notify"]), ("false", "true"))

    def test_read_package_false_sends_only_the_inputs(self):
        result = self.run_action(self.plugin_zip(), DLM_VERSION="1.3.0", DLM_READ_PACKAGE="false")

        self.assertPublished(result)
        for field in ("tested_wp", "requires_wp", "requires_php", "changelog", "upgrade_notice"):
            self.assertNotIn(field, Store.requests[0]["fields"])

    def test_extra_fields(self):
        result = self.run_action(self.plugin_zip(), DLM_VERSION="1.3.0", DLM_FIELDS="custom_a = one\n\ncustom_b=two=2\n")

        self.assertPublished(result)
        self.assertEqual(Store.requests[0]["fields"]["custom_a"], " one")
        self.assertEqual(Store.requests[0]["fields"]["custom_b"], "two=2")

    def test_reserved_extra_fields_are_refused(self):
        result = self.run_action(self.plugin_zip(), DLM_VERSION="1.3.0", DLM_FIELDS="version=9.9.9")

        self.assertFailed(result, "Set 'version' with its own input")
        self.assertEqual(Store.requests, [])

    def test_file_names_with_special_characters(self):
        file = self.plugin_zip('my plugin;v1,final.zip')
        result = self.run_action(file, DLM_VERSION="1.3.0")

        self.assertPublished(result)
        self.assertEqual(Store.requests[0]["files"]["file"]["filename"], 'my plugin;v1,final.zip')
        self.assertEqual(Store.requests[0]["files"]["file"]["data"], file.read_bytes())

    def test_already_published(self):
        Store.responses.append(created_response({"version": "1.3.0", "make_current": "true"}, created=False))
        result = self.run_action(self.plugin_zip(), DLM_VERSION="1.3.0")

        self.assertPublished(result)
        self.assertEqual(result.outputs["created"], "false")
        self.assertIn("already published with this file", result.stdout)

    def test_the_version_input_takes_tag_names(self):
        result = self.run_action(self.plugin_zip(), DLM_VERSION="example-plugin-pro-v1.3.0-beta.2")

        self.assertPublished(result)
        self.assertEqual(Store.requests[0]["fields"]["version"], "1.3.0-beta.2")

    def test_needs_a_version_outside_tags(self):
        result = self.run_action(self.plugin_zip(), GITHUB_REF_TYPE="branch", GITHUB_REF_NAME="main")
        self.assertFailed(result, "Set the version input")

        result = self.run_action(self.plugin_zip(), GITHUB_REF_TYPE="tag", GITHUB_REF_NAME="release-latest")
        self.assertFailed(result, "Could not read a version from the tag 'release-latest'")
        self.assertEqual(Store.requests, [])

    def test_validates_inputs(self):
        self.assertFailed(self.run_action(self.plugin_zip(), DLM_VERSION="1.3.0", DLM_SOFTWARE_ID="abc"), "software-id")
        self.assertFailed(self.run_action(self.tmp / "missing.zip", DLM_VERSION="1.3.0"), "does not exist")
        self.assertFailed(self.run_action(self.plugin_zip(), DLM_VERSION="1.3.0", DLM_CHANNEL="nightly"), "channel must be")
        self.assertFailed(self.run_action(self.plugin_zip(), DLM_VERSION="1.3.0", DLM_NOTIFY="maybe"), "notify must be true or false")
        self.assertFailed(self.run_action(self.plugin_zip(), DLM_VERSION="latest"), "The version must look like 1.2.3")
        self.assertEqual(Store.requests, [])

    def test_errors_carry_the_store_message_and_a_hint(self):
        cases = [
            (error_response(409, "release_exists", "Version 1.3.0 already exists with a different file."), "Publish a new version instead"),
            (error_response(500, "dlm_rest_route_disabled_error", "This route is disabled via the plugin settings."), "Enable route 034"),
            (error_response(401, "authentication_error", "Consumer secret is invalid."), "Write or Read & Write permission"),
            (error_response(404, "rest_no_route", "No route was found."), "Pro 2.1.1 or newer"),
            ((500, {"Content-Type": "text/html"}, b"<b>Fatal error</b>: Uncaught Error"), "Fatal error: Uncaught Error"),
            ((200, {"Content-Type": "text/html"}, b"<br /><b>Fatal error</b>: boom"), "unexpected response"),
        ]
        for response, hint in cases:
            with self.subTest(hint=hint):
                Store.requests.clear()
                Store.responses.append(response)
                result = self.run_action(self.plugin_zip(), DLM_VERSION="1.3.0")
                self.assertFailed(result, hint)
                self.assertEqual(len(Store.requests), 1, "errors are not retried")

    def test_retries_temporary_unavailability(self):
        Store.responses.append((503, {}, b"Service Unavailable"))
        result = self.run_action(self.plugin_zip(), DLM_VERSION="1.3.0")

        self.assertPublished(result)
        self.assertEqual(len(Store.requests), 2)
        self.assertIn("Attempt 1 failed (HTTP 503)", result.stdout)

    def test_gives_up_after_three_attempts(self):
        Store.responses.extend([(502, {}, b"Bad Gateway")] * 3)
        result = self.run_action(self.plugin_zip(), DLM_VERSION="1.3.0")

        self.assertFailed(result, "HTTP 502")
        self.assertEqual(len(Store.requests), 3)

    def test_redirects_are_reported(self):
        Store.responses.append((301, {"Location": "https://www.example.com/wp-json/dlm/v1/software/12/releases"}, b""))
        result = self.run_action(self.plugin_zip(), DLM_VERSION="1.3.0")

        self.assertFailed(result, "redirected to https://www.example.com/")

    def test_url_forms(self):
        for url in (self.url + "/", self.url + "/wp-json", self.url + "/wp-json/dlm/v1"):
            with self.subTest(url=url):
                Store.requests.clear()
                self.assertPublished(self.run_action(self.plugin_zip(), DLM_VERSION="1.3.0", DLM_URL=url))
                self.assertEqual(Store.requests[0]["path"], "/wp-json/dlm/v1/software/12/releases")


if __name__ == "__main__":
    unittest.main()
