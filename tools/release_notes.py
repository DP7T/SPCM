"""Gives every GitHub release a clear name and description, so nobody has to remember
which version is which.

  name:  "SPCM 1.2.1 - testing" / "SPCM 1.0.0 - stable" / "SPCM 1.1.0 - old test version"
  body:  one line saying what it is and which one to use, then that version's part of
         CHANGELOG.md, then the install note.

Run by the workflow "Release notes" (needs GH_TOKEN and GITHUB_REPOSITORY).
Releases whose version has no section in this CHANGELOG.md keep their text, only the
name and the first line change.
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL = ("**Install / update:** download the zip below, unzip it over your old SPCM "
           "folder and start **SPCM.exe** (or *SPCM (simulation).bat* to try without "
           "hardware). Your settings (config.yaml) and data are kept.")
MARK = "<!-- spcm-notes -->"


def ver(tag):
    return tuple(int(x) for x in re.findall(r"\d+", tag)[:3])


def sections(text):
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"## (\d+\.\d+\.\d+)", line)
        if m:
            cur = m.group(1)
            out[cur] = []
        elif cur:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def gh(*args, data=None):
    r = subprocess.run(["gh", "api", *args], input=json.dumps(data) if data else None,
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        sys.exit(r.stderr)
    return json.loads(r.stdout or "null")


def describe(rels):
    """status word and first line for every tag"""
    stable = [r for r in rels if not r["prerelease"]]
    best = max(stable, key=lambda r: ver(r["tag_name"]))["tag_name"] if stable else None
    sv = ver(best) if best else (0, 0, 0)
    newest = {}                                   # newest patch of every x.y
    for r in rels:
        k = ver(r["tag_name"])[:2]
        if k not in newest or ver(r["tag_name"]) > ver(newest[k]):
            newest[k] = r["tag_name"]
    out = {}
    for r in rels:
        t, v = r["tag_name"], ver(r["tag_name"])
        if t == best:
            out[t] = ("stable", "✅ **Stable version - use this one for measurements.**")
        elif not r["prerelease"]:
            out[t] = ("old", f"Older stable version. The current one is {best}.")
        elif v > sv and newest[v[:2]] == t:
            later = [n for n in newest.values() if ver(n) > v]
            out[t] = ("testing", "🧪 **Testing version** - new features to try on the "
                      "microscope and report back."
                      + (f" {max(later, key=ver)} contains everything in it too." if later else "")
                      + (f" For measurements use {best}." if best else ""))
        else:
            newer = newest[v[:2]] if newest[v[:2]] != t else best
            out[t] = ("old test version", f"⏸ **Old test version** - replaced by {newer}, "
                      "no need to download it.")
    return out


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        notes = sections(f.read())
    rels = [r for r in gh(f"repos/{repo}/releases?per_page=100") if not r["draft"]]
    for r in rels:
        t = r["tag_name"]
        word, first = describe(rels)[t]
        v = ".".join(map(str, ver(t)))
        old = r.get("body") or ""
        prev = [ver(x["tag_name"]) for x in rels
                if not x["prerelease"] and ver(x["tag_name"]) < ver(t)]
        if not r["prerelease"] and prev and v in notes:
            # stable: everything since the previous stable version, newest first
            p0 = max(prev)
            since = [k for k in notes if p0 < tuple(map(int, k.split("."))) <= ver(t)]
            what = (f"### What's new since v{'.'.join(map(str, p0))}\n\n"
                    + "\n\n".join(f"#### {k}\n{notes[k]}" for k in since))
        elif v in notes:
            what = "### What's in it\n" + notes[v]
        elif MARK in old:                       # keep what an earlier run wrote
            what = old.split(MARK, 1)[1].replace(INSTALL, "").strip()
        elif old.startswith("Windows build of"):  # the old automatic text: nothing to keep
            what = ""
        else:
            what = old.strip()
        body = "\n\n".join(x for x in (f"{first}\n{MARK}", what, INSTALL) if x)
        name = f"SPCM {v} - {word}"
        if body.strip() != old.strip() or name != r["name"]:
            gh("-X", "PATCH", f"repos/{repo}/releases/{r['id']}", "--input", "-",
               data={"name": name, "body": body})
            print("updated", t, "->", name)
        else:
            print("unchanged", t)


if __name__ == "__main__":
    main()
