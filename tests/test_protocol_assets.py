"""Offline checks for the public export-asset path matcher."""

import unittest

from eighteenbirdies.protocol_assets import (
    EXPORT_PATH,
    LOGIN_PATH,
    imported_scripts,
    linked_scripts,
    paths_present,
)


PAGE = """
<html><head>
<script type="module" src="/_astro/SignIn.example.js"></script>
<script src="https://cdn.example/tracker.js"></script>
<link rel="modulepreload" href="/_astro/DownloadAccountData.example.js">
<astro-island component-url="/_astro/DownloadAccountData.41460882.js" renderer-url="/_astro/client.example.js"></astro-island>
<script src="https://user:secret@18birdies.com/private.js"></script>
<script src="http://18birdies.com/insecure.js"></script>
</head></html>
"""


class ProtocolAssetTests(unittest.TestCase):
    def test_only_same_host_https_scripts_are_kept(self):
        self.assertEqual(
            linked_scripts(PAGE),
            [
                "https://18birdies.com/_astro/SignIn.example.js",
                "https://18birdies.com/_astro/DownloadAccountData.example.js",
                "https://18birdies.com/_astro/DownloadAccountData.41460882.js",
                "https://18birdies.com/_astro/client.example.js",
            ],
        )

    def test_relative_chunk_imports_stay_on_the_official_host(self):
        javascript = (
            'import{b as m}from"./birdiesFetch.example.js";'
            'import{T as h}from"./SignIn.example.js";'
            'const other="https://cdn.example/tracker.js";'
            'const duplicate="_astro/SignIn.example.js";'
            'import "/_astro/hooks.example.js";'
            'import "https://18birdies.com/_astro/client.example.js";'
            'import "https://18birdies.com/_astro/nested/nope.js";'
        )
        self.assertEqual(
            imported_scripts(
                javascript, "https://18birdies.com/_astro/DownloadAccountData.example.js"
            ),
            [
                "https://18birdies.com/_astro/birdiesFetch.example.js",
                "https://18birdies.com/_astro/SignIn.example.js",
                "https://18birdies.com/_astro/hooks.example.js",
                "https://18birdies.com/_astro/client.example.js",
            ],
        )

    def test_required_paths_must_both_be_named(self):
        self.assertEqual(
            paths_present(f"post {LOGIN_PATH}", f"post {EXPORT_PATH}"),
            {LOGIN_PATH: True, EXPORT_PATH: True},
        )
        self.assertFalse(paths_present("no endpoints here")[LOGIN_PATH])
        self.assertFalse(paths_present(LOGIN_PATH)[EXPORT_PATH])


if __name__ == "__main__":
    unittest.main()
