import unittest

import build_index as b

BADGING = """package: name='dev.resonance.player' versionCode='7' versionName='1.2.1' compileSdkVersion='35'
sdkVersion:'26'
targetSdkVersion:'35'
uses-permission: name='android.permission.WAKE_LOCK'
uses-permission: name='android.permission.READ_MEDIA_AUDIO'
uses-permission-sdk-23: name='android.permission.READ_EXTERNAL_STORAGE'
"""

SIGNER = """Verifies
Verified using v2 scheme (APK Signature Scheme v2): true
Number of signers: 1
Signer #1 certificate DN: CN=Android Debug
Signer #1 certificate SHA-256 digest: AABBCCDDEEFF00112233445566778899AABBCCDDEEFF00112233445566778899
"""

META = {"package": "dev.a.b", "name": "A", "summary": "s", "category": "Music",
        "repo": "https://github.com/me/a", "license": "MIT"}


class Tests(unittest.TestCase):
    def test_badging(self):
        info = b.parse_badging(BADGING)
        self.assertEqual(info["package"], "dev.resonance.player")
        self.assertEqual(info["versionCode"], 7)
        self.assertEqual(info["minSdk"], 26)
        self.assertEqual(len(info["permissions"]), 3)

    def test_signer(self):
        self.assertEqual(b.parse_apksigner(SIGNER), "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899")
        v3 = "Verifies\nSigner (minSdkVersion=26, maxSdkVersion=2147483647) certificate SHA-256 digest: " + "ab" * 32 + "\n"
        self.assertEqual(b.parse_apksigner(v3), "ab" * 32)
        with self.assertRaises(b.AppError):
            b.parse_apksigner("DOES NOT VERIFY")

    def test_validate_ok(self):
        self.assertEqual(b.validate_meta(dict(META), "dev.a.b.yml"), ("me", "a"))

    def test_validate_problems(self):
        for change, name in [
            ({"license": "Proprietary"}, "dev.a.b.yml"),
            ({"category": "Stuff"}, "dev.a.b.yml"),
            ({"repo": "https://gitlab.com/me/a"}, "dev.a.b.yml"),
            ({"package": "nodots"}, "nodots.yml"),
            ({"summary": "x" * 81}, "dev.a.b.yml"),
            ({"signer": "xyz"}, "dev.a.b.yml"),
            ({}, "wrong-name.yml"),
        ]:
            meta = dict(META, **change)
            with self.assertRaises(b.AppError, msg=str(change)):
                b.validate_meta(meta, name)

    def test_pick_asset(self):
        release = {"assets": [
            {"name": "app-debug.apk"}, {"name": "app-release.apk"},
            {"name": "app-unsigned.apk"}, {"name": "notes.txt"}]}
        self.assertEqual(b.pick_asset(release)["name"], "app-release.apk")
        self.assertIsNone(b.pick_asset({"assets": [{"name": "x.zip"}]}))

    def test_reuse_needs_same_asset(self):
        asset = {"browser_download_url": "https://x/a.apk", "updated_at": "t1", "size": 5}
        old = [{"apk": "https://x/a.apk", "assetUpdatedAt": "t1", "size": 5, "sha256": "aa"}]
        self.assertIsNotNone(b.reusable(old, asset))
        self.assertIsNone(b.reusable(old, dict(asset, updated_at="t2")))


if __name__ == "__main__":
    unittest.main()
