# OpenAndroid Index

The catalog behind **OpenAndroid Store**: a list of open-source Android apps. The store app reads
`index.json`, published from this repository by GitHub Actions.

## How it works

1. Each app is one file, `apps/<package>.yml`, with a short description and a link to its GitHub repository.
2. A GitHub Action reads the app's latest **GitHub Releases**, downloads each APK and records its SHA-256,
   package name, version, minimum Android version, permissions and the SHA-256 of its signing certificate.
3. The result is published as `index.json` on GitHub Pages (rebuilt on every merge and every 6 hours).
4. The store checks the checksum, package name and signing key again before installing anything.

## Add your app

1. Fork this repository.
2. Copy `apps/dev.resonance.player.yml` to `apps/<your.package.name>.yml` and fill it in.
3. Optionally add screenshots in `screenshots/<your.package.name>/` (PNG, JPG or WebP, up to 8 files of 3 MB, sorted by file name).
4. Open a pull request. A check downloads your APK and verifies it.

```yaml
package: com.example.app        # must equal the applicationId, and the file name
name: Example
summary: One line, 80 characters at most
category: Tools                 # see the list below
repo: https://github.com/you/example
license: MIT                    # SPDX id of an open-source license
author: you                     # optional
signer: <sha256 of your cert>   # optional: pin your signing key so it can never change by mistake
website: https://example.com    # optional
issues: https://github.com/you/example/issues   # optional, defaults to <repo>/issues
description: |
  A few sentences about the app.
```

### Icons

The store needs a raster icon. The script looks for one in this order:

1. `icons/<package>.png` (or `.webp`) in this repository, square, 512×512 recommended
2. `fastlane/metadata/android/en-US/images/icon.png` in your app's repository
3. the icon published last time
4. a PNG or WebP icon found inside the APK itself

Apps that only have an adaptive (XML) launcher icon, like most Compose apps, have nothing to extract:
add option 1 or 2. Without any icon the store shows a colored letter.

Categories: Communication, Development, Education, Games, Media, Music, Other, Productivity, Security, System, Tools.

### Rules

- The source code must be public and under an open-source license (MIT, Apache-2.0, GPL, and so on).
- The latest release must have a **signed** APK attached (a file named `*-release.apk` is preferred; `*unsigned*` is ignored).
- The APK's package name must match `package`.
- All versions must be signed with the **same key**; Android refuses updates signed with another key.
- Only stable releases are listed: drafts and pre-releases are skipped.
- Every pull request is reviewed by hand. Apps with hidden trackers, malware or closed-source parts are rejected.

## Run it locally

```
pip install pyyaml
python -m unittest discover -s scripts -p "test_*.py"
GITHUB_TOKEN=... python scripts/build_index.py --check --out /tmp/out
```

Needs Android build-tools (`aapt2`, `apksigner`) on `PATH` or `ANDROID_HOME` set.

## Set up your own copy

1. Create a repository named `OpenAndroid-Index` and push this code.
2. **Settings → Pages → Source: GitHub Actions.**
3. **Settings → Branches:** protect `main`, require pull requests and a review (so every app is checked by hand).
4. Run **Actions → Publish catalog** once. The catalog appears at `https://<user>.github.io/OpenAndroid-Index/index.json`.
5. Put that address into the store (`DEFAULT_INDEX_URL` in `app/build.gradle.kts`, or Settings → Catalog address).

## `index.json` format

```json
{
  "format": 1,
  "generatedAt": 1760000000000,
  "apps": [{
    "package": "dev.example", "name": "Example", "summary": "…", "description": "…",
    "category": "Tools", "repo": "https://github.com/…", "license": "MIT", "author": "…",
    "icon": "icons/dev.example.png",
    "versions": [{
      "versionName": "1.0.0", "versionCode": 1, "minSdk": 26,
      "apk": "https://github.com/…/app.apk", "size": 123456,
      "sha256": "…", "signer": "…", "permissions": ["android.permission.CAMERA"],
      "changelog": "…", "publishedAt": "2026-11-09T08:00:00Z"
    }]
  }]
}
```

License: MIT.
