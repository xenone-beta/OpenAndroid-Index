#!/usr/bin/env python3
"""Builds index.json for OpenAndroid Store from the app descriptions in apps/.

For every apps/<package>.yml it reads the latest GitHub releases of the app's repository,
downloads the APK of each, and records: SHA-256 of the file, package name, version, minimum
Android version, permissions and the SHA-256 of the signing certificate.

  --check   validate everything and fail on any problem (used for pull requests)
  default   build the catalog; a broken app keeps its previous entry instead of vanishing
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

FORMAT = 1
MAX_RELEASES = 8
MAX_APK_BYTES = 200 * 1024 * 1024

CATEGORIES = [
    "Communication", "Development", "Education", "Games", "Media", "Music",
    "Other", "Productivity", "Security", "System", "Tools",
]

# Licenses accepted for the catalog (SPDX identifiers). Only open-source licenses.
LICENSES = {
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0", "0BSD",
    "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later",
    "LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later",
    "AGPL-3.0-only", "AGPL-3.0-or-later", "EUPL-1.2", "Unlicense", "CC0-1.0",
}

PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
REPO_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)/?$")
REQUIRED = ["package", "name", "summary", "category", "repo", "license"]


class AppError(Exception):
    """Something is wrong with one app. Carries a message for the pull request author."""


# ---- Validation ---------------------------------------------------------------------------

def validate_meta(meta, filename):
    """Checks an app description. Returns (owner, repo)."""
    if not isinstance(meta, dict):
        raise AppError("the file is not a YAML mapping")
    for key in REQUIRED:
        if not str(meta.get(key, "")).strip():
            raise AppError(f"missing field '{key}'")
    package = str(meta["package"])
    if not PACKAGE_RE.match(package):
        raise AppError(f"'{package}' is not a valid Android package name")
    if filename != f"{package}.yml":
        raise AppError(f"the file must be named {package}.yml")
    if meta["category"] not in CATEGORIES:
        raise AppError(f"category must be one of: {', '.join(CATEGORIES)}")
    if meta["license"] not in LICENSES:
        raise AppError("license must be an open-source SPDX identifier such as MIT or GPL-3.0-only")
    match = REPO_RE.match(str(meta["repo"]))
    if not match:
        raise AppError("repo must look like https://github.com/owner/name")
    if len(str(meta["summary"])) > 80:
        raise AppError("summary must be 80 characters or fewer")
    signer = str(meta.get("signer", "")).lower()
    if signer and not re.fullmatch(r"[0-9a-f]{64}", signer):
        raise AppError("signer must be a 64-character hex SHA-256 of the signing certificate")
    return match.group(1), match.group(2)


# ---- GitHub -------------------------------------------------------------------------------

def http_get(url, token=None, accept=None):
    headers = {"User-Agent": "OpenAndroidIndex"}
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    # GitHub sometimes answers 502/503/504 for a moment; try a few times.
    for attempt in range(4):
        try:
            return urllib.request.urlopen(request, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code not in (500, 502, 503, 504) or attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
        time.sleep(2 * (attempt + 1))


def fetch_releases(owner, repo, token):
    url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page={MAX_RELEASES}"
    try:
        with http_get(url, token, "application/vnd.github+json") as response:
            releases = json.load(response)
    except urllib.error.HTTPError as e:
        raise AppError(f"cannot read releases of {owner}/{repo} (HTTP {e.code})")
    return [r for r in releases if not r.get("draft") and not r.get("prerelease")]


def pick_asset(release):
    """The APK of a release: prefer a name with 'release', avoid 'debug', never 'unsigned'."""
    apks = [a for a in release.get("assets", []) if a.get("name", "").lower().endswith(".apk")]
    apks = [a for a in apks if "unsigned" not in a["name"].lower()]
    if not apks:
        return None
    def rank(asset):
        name = asset["name"].lower()
        return (("debug" in name), not ("release" in name), name)
    return sorted(apks, key=rank)[0]


# ---- APK inspection -----------------------------------------------------------------------

def find_tool(name):
    found = shutil.which(name)
    if found:
        return found
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if sdk:
        candidates = sorted(Path(sdk, "build-tools").glob(f"*/{name}"), reverse=True)
        if candidates:
            return str(candidates[0])
    raise SystemExit(f"{name} not found. Install Android build-tools or set ANDROID_HOME.")


def parse_badging(text):
    """Reads `aapt2 dump badging` output."""
    m = re.search(r"package: name='([^']+)' versionCode='(\d+)' versionName='([^']*)'", text)
    if not m:
        raise AppError("could not read the package info from the APK")
    sdk = re.search(r"^sdkVersion:'(\d+)'", text, re.M)
    perms = sorted(set(re.findall(r"^uses-permission(?:-sdk-23)?: name='([^']+)'", text, re.M)))
    return {
        "package": m.group(1),
        "versionCode": int(m.group(2)),
        "versionName": m.group(3),
        "minSdk": int(sdk.group(1)) if sdk else 1,
        "permissions": perms,
    }


def parse_apksigner(text):
    """Reads `apksigner verify --print-certs` output. Returns the certificate SHA-256 of the signer."""
    # Formats seen in the wild: "Signer #1 certificate ...", "Signer (minSdkVersion=24, ...) certificate ..."
    # and, in the newest build-tools, "V2 Signer: certificate ...".
    digests = re.findall(r"Signer(?:\s+#\d+|\s*\([^)]*\))?:?\s+certificate SHA-256 digest:\s*([0-9a-fA-F]{64})", text)
    if not digests:
        raise AppError("could not read the signing certificate from apksigner output: " + text.strip()[:300])
    if len(set(d.lower() for d in digests)) > 1:
        raise AppError("the APK has several different signers")
    return digests[0].lower()


def inspect_apk(path, aapt2, apksigner):
    badging = subprocess.run([aapt2, "dump", "badging", path], capture_output=True, text=True)
    if badging.returncode != 0:
        raise AppError("aapt2 could not read the APK")
    info = parse_badging(badging.stdout)
    signed = subprocess.run([apksigner, "verify", "--print-certs", path], capture_output=True, text=True)
    if signed.returncode != 0:
        raise AppError("the APK signature does not verify")
    info["signer"] = parse_apksigner(signed.stdout)
    return info


def download(url, target):
    sha = hashlib.sha256()
    size = 0
    with http_get(url) as response, open(target, "wb") as out:
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_APK_BYTES:
                raise AppError("the APK is larger than 200 MB")
            sha.update(chunk)
            out.write(chunk)
    return sha.hexdigest(), size


# ---- Building -----------------------------------------------------------------------------

def reusable(previous_versions, asset):
    """A version from the last catalog that still describes this exact release asset."""
    for v in previous_versions:
        if v.get("apk") == asset["browser_download_url"] and v.get("assetUpdatedAt") == asset.get("updated_at") \
                and v.get("size") == asset.get("size"):
            return v
    return None


def build_app(meta, owner, repo, previous_app, token, tools, workdir):
    package = str(meta["package"])
    pinned = str(meta.get("signer", "")).lower()
    previous_versions = (previous_app or {}).get("versions", [])
    versions = []
    problems = []

    for release in fetch_releases(owner, repo, token):
        asset = pick_asset(release)
        if asset is None:
            continue
        try:
            cached = reusable(previous_versions, asset)
            if cached:
                version = dict(cached)
            else:
                target = os.path.join(workdir, asset["name"])
                sha256, size = download(asset["browser_download_url"], target)
                info = inspect_apk(target, *tools)
                os.remove(target)
                if info["package"] != package:
                    raise AppError(f"the APK is '{info['package']}', expected '{package}'")
                version = {
                    "versionName": info["versionName"],
                    "versionCode": info["versionCode"],
                    "minSdk": info["minSdk"],
                    "apk": asset["browser_download_url"],
                    "size": size,
                    "sha256": sha256,
                    "signer": info["signer"],
                    "permissions": info["permissions"],
                    "assetUpdatedAt": asset.get("updated_at", ""),
                }
            if pinned and version["signer"] != pinned:
                raise AppError("the signing key differs from the 'signer' pinned in the YAML file")
            version["changelog"] = (release.get("body") or "").strip()[:4000]
            version["publishedAt"] = release.get("published_at") or ""
            versions.append(version)
        except AppError as e:
            problems.append(f"release {release.get('tag_name')}: {e}")

    if not versions:
        raise AppError("no installable release found" + (": " + "; ".join(problems) if problems else ""))

    # One app, one signing key: Android refuses updates signed with another key.
    versions.sort(key=lambda v: v["versionCode"], reverse=True)
    key = versions[0]["signer"]
    for v in versions:
        if v["signer"] != key:
            problems.append(f"version {v['versionName']} is signed with a different key and was left out")
    versions = [v for v in versions if v["signer"] == key]

    app = {
        "package": package,
        "name": str(meta["name"]),
        "summary": str(meta["summary"]),
        "description": str(meta.get("description", "")).strip(),
        "category": meta["category"],
        "repo": str(meta["repo"]).rstrip("/"),
        "license": meta["license"],
        "author": str(meta.get("author") or owner),
        "versions": versions,
    }
    return app, problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apps", default="apps")
    parser.add_argument("--icons", default="icons")
    parser.add_argument("--out", default="public")
    parser.add_argument("--previous", default=None, help="the currently published index.json, to avoid re-downloading")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    tools = (find_tool("aapt2"), find_tool("apksigner"))
    previous = {}
    if args.previous and os.path.isfile(args.previous):
        try:
            with open(args.previous, encoding="utf-8") as f:
                previous = {a["package"]: a for a in json.load(f).get("apps", [])}
        except (ValueError, KeyError, TypeError):
            previous = {}

    out = Path(args.out)
    (out / "icons").mkdir(parents=True, exist_ok=True)
    apps, failures, seen = [], [], set()

    with tempfile.TemporaryDirectory() as workdir:
        for path in sorted(Path(args.apps).glob("*.yml")):
            try:
                with open(path, encoding="utf-8") as f:
                    meta = yaml.safe_load(f)
                owner, repo = validate_meta(meta, path.name)
                if meta["package"] in seen:
                    raise AppError("duplicate package")
                seen.add(meta["package"])
                app, problems = build_app(meta, owner, repo, previous.get(meta["package"]), token, tools, workdir)
                icon = Path(args.icons) / f"{meta['package']}.png"
                if icon.is_file():
                    shutil.copy(icon, out / "icons" / icon.name)
                    app["icon"] = f"icons/{icon.name}"
                apps.append(app)
                for problem in problems:
                    print(f"::warning file={path}::{problem}")
                print(f"ok    {meta['package']}  ({len(app['versions'])} versions)")
            except (AppError, OSError, yaml.YAMLError, urllib.error.URLError) as e:
                failures.append((path, str(e)))
                print(f"::error file={path}::{e}")
                old = previous.get(path.stem)
                if old and not args.check:
                    apps.append(old)  # keep the last good entry rather than dropping the app
                    print(f"keep  {path.stem}  (previous entry)")

    if args.check:
        print(f"\n{len(apps)} app(s) checked, {len(failures)} problem(s)")
        sys.exit(1 if failures else 0)

    if not apps:
        raise SystemExit("no apps were built; refusing to publish an empty catalog")
    apps.sort(key=lambda a: a["name"].lower())
    index = {"format": FORMAT, "generatedAt": int(time.time() * 1000), "apps": apps}
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>OpenAndroid Index</title>"
        "<p>This is the catalog of <b>OpenAndroid Store</b>. Open <a href=index.json>index.json</a> "
        "or install the app to browse it.</p>", encoding="utf-8")
    print(f"\nwrote {out / 'index.json'} with {len(apps)} app(s)")


if __name__ == "__main__":
    main()
