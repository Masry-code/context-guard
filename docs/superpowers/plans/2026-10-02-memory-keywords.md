# Memory keywords Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every memory carries keywords. A chat is told, in one cheap line, when another project's memory matches the author's message, and it can search all memories itself with `--recall`.

**Architecture:** All new code goes into `guard.py`, in one new section placed just before `cmd_bootstrap`. That's the memory section, and the file is one module by design. The parts:
- a frontmatter reader;
- a cached catalogue of every memory folder;
- one scorer shared by the prompt hook (`memory_hints`, strict) and the CLI (`--recall`, loose);
- a Stop-hook check (`keywords_block`) that sends back a memory saved without keywords.

Tests drive the hooks as subprocesses against a throwaway home, as the rest of `test_guard.py` does. Internals go through `guard_call`.

**Tech Stack:** Python 3 standard library only. The suite is `python test_guard.py`, and the full gate is `python "D:/AI Projects/Claude Needed Tools/safeguard-c/suite.py"`.

**Spec:** `docs/superpowers/specs/2026-10-02-memory-keywords-design.md`

---

## Machine rules (every task)

- `guard.py` is **CRLF**: 4090 CRLF and 0 bare LF at `390e7ee`. Never use `sed -i`, a heredoc or a shell redirect on it.
  - Patch it ONLY with a Python script written with the Write tool, which opens the file with `io.open(path, newline="")`.
  - Each anchor must occur exactly once, or the script aborts.
  - New text is inserted with `\r\n` endings.
  - Print the CRLF and bare-LF counts before and after. Bare LF must stay 0.
- `test_guard.py` and `README.md` are **LF**. Edit them with the Edit tool.
- Every new test function also goes into the explicit runner tuple at the bottom of `test_guard.py`, the `for t in (` near line 7544, after `test_an_entry_has_a_short_stable_id_and_the_flag_takes_it`. A test that's left out never runs.
- Fixture names are invented. No real project names: the pre-push names-guard refuses them.
- Commit author: `Masry-code <Masry-code@users.noreply.github.com>`, which is the repo default. End every message with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Never push. The controller pushes after review.

## Shared test helpers (added in Task 1, used by every task)

Put these just above the Task 1 tests, at the end of the test definitions and before the `if __name__` runner block:

```python
# ------------------------------------------------------------------ memory keywords
def mem_text(slug, desc, kws=None, eol="\n"):
    """A memory file the way Claude Code writes one, with an optional keywords line."""
    lines = ["---", "name: " + slug, 'description: "' + desc + '"']
    if kws is not None:
        lines.append("keywords: " + ", ".join(kws))
    lines += ["metadata:", "  type: project", "---", "", "body of " + slug]
    return eol.join(lines) + eol


def kw_home(memories, lists=None, manifest=None, claude_md=None):
    """A throwaway home holding memory files. memories: {(key, slug): text};
    lists: {key: MEMORY.md text}; manifest: the memory-manifest.json dict."""
    home = make_home({})
    for (key, slug), body in memories.items():
        d = os.path.join(home, ".claude", "projects", key, "memory")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, slug + ".md"), "w", encoding="utf-8", newline="") as f:
            f.write(body)
    for key, text in (lists or {}).items():
        d = os.path.join(home, ".claude", "projects", key, "memory")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write(text)
    state = os.path.join(home, ".claude", "context-guard")
    os.makedirs(state, exist_ok=True)
    if manifest is not None:
        with open(os.path.join(state, "memory-manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f)
    if claude_md is not None:
        with open(os.path.join(home, ".claude", "CLAUDE.md"), "w", encoding="utf-8") as f:
            f.write(claude_md)
    return home


def kw_json(home, code):
    """Run `code` (which must print one JSON value) inside guard, and parse it."""
    p = guard_call(home, code)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"_error": (p.stderr or p.stdout)[-400:]}
```

---

### Task 1: The frontmatter reader

**Files:**
- Modify: `guard.py`. Insert a new section directly above `def cmd_bootstrap():`, anchor `\r\ndef cmd_bootstrap():\r\n`.
- Test: `test_guard.py`

- [ ] **Step 1: Write the failing test** (plus the shared helpers above)

```python
def test_the_keywords_reader_reads_the_line_and_falls_back_to_the_summary():
    """Spec 2026-10-02 section 1: a keywords line is read as given; a file without one
    still works, from its slug and description; CRLF and a BOM change nothing."""
    home = kw_home({
        (KEY, "android-adb"): mem_text("android-adb", "adb on the phone",
                                       ["adb", "Android Phone", "usb debugging"]),
        (KEY, "crlf-one"): mem_text("crlf-one", "x", ["alpha", "beta"], eol="\r\n"),
        (KEY, "telegram-bot-traps"): mem_text("telegram-bot-traps",
                                              "Telegram polling and webhook conflict"),
        (KEY, "no-front"): "# just a heading\n",
    })
    try:
        p = os.path.join(home, ".claude", "projects", KEY, "memory")
        with open(os.path.join(p, "bom-one.md"), "w", encoding="utf-8-sig") as f:
            f.write(mem_text("bom-one", "y", ["gamma"]))
        r = kw_json(home, "import json, os\n"
                    "p = %r\n"
                    "print(json.dumps({n: guard.memory_keywords(os.path.join(p, n + '.md'))\n"
                    "  for n in ['android-adb','crlf-one','telegram-bot-traps','no-front','bom-one']}))"
                    % p)
        a = r.get("android-adb") or {}
        check("kw-read: keywords are read and lowercased",
              a.get("keywords") == ["adb", "android phone", "usb debugging"], repr(a))
        check("kw-read: a written line is not derived", a.get("derived") is False, repr(a))
        check("kw-read: the description is read without its quotes",
              a.get("description") == "adb on the phone", repr(a))
        check("kw-read: CRLF changes nothing",
              (r.get("crlf-one") or {}).get("keywords") == ["alpha", "beta"], repr(r.get("crlf-one")))
        check("kw-read: a BOM changes nothing",
              (r.get("bom-one") or {}).get("keywords") == ["gamma"], repr(r.get("bom-one")))
        t = r.get("telegram-bot-traps") or {}
        check("kw-read: no line -> derived from slug and description",
              t.get("derived") is True and "telegram" in t.get("keywords", [])
              and "webhook" in t.get("keywords", []), repr(t))
        check("kw-read: derived words are 4+ chars, at most 8",
              all(len(w) >= 4 for w in t.get("keywords", [])) and len(t.get("keywords", [])) <= 8,
              repr(t))
        check("kw-read: no frontmatter -> None", r.get("no-front") is None, repr(r.get("no-front")))
    finally:
        rm_tree(home)
```

Add `test_the_keywords_reader_reads_the_line_and_falls_back_to_the_summary` to the runner tuple.

- [ ] **Step 2: Run it and watch it fail**

Run: `python test_guard.py 2>&1 | grep -E "kw-read|AttributeError" | head`
Expected: FAIL lines, with `AttributeError: module 'guard' has no attribute 'memory_keywords'` in the detail.

- [ ] **Step 3: Implement.** Insert above `def cmd_bootstrap():`:

```python
# --------------------------------------------------- memory keywords (spec 2026-10-02)
# His ask, 25 Sep 2026: "can you put keywords for each memory so when a new chat with a
# different topic needs sth it can take it from the other folder memory??". Every memory
# lives in one shared folder, but a chat only SEES its own project's list - the memory that
# would have saved an hour sits one folder away, unread. Keywords let the prompt hook tell
# the chat, in one line, that it exists; --recall lets the chat search for itself mid-task.
KEYWORDS_RE = re.compile(r"^keywords\s*:\s*(.*)$", re.I)
WORD_RE = re.compile(r"[^\W_]+")       # letters and digits in ANY script - Arabic included
FRONT_LINES = 40
DERIVED_MAX = 8
KW_STOP = LABEL_STOP | {"when", "what", "have", "into", "never", "only", "they", "them",
                        "then", "than", "were", "will", "your", "does", "done", "each",
                        "every", "must", "also", "because", "before", "after", "still",
                        "here", "there", "which", "while", "used", "uses", "same"}


def _words(text):
    return WORD_RE.findall((text or "").lower())


def memory_keywords(path):
    """A memory's slug, description and keywords, read from its frontmatter only.

    No keywords line - every memory written before 2 Oct 2026, and any chat that forgets -
    still works: the words of the slug and the description stand in, marked derived, and the
    scorer asks more of them. Returns None for a file with no frontmatter or one it cannot
    read; the catalogue skips it."""
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            head = []
            for i, line in enumerate(f):
                if i >= FRONT_LINES:
                    break
                head.append(line.rstrip("\r\n"))
    except Exception as e:
        log("keywords: cannot read %s (%r)" % (path, e))
        return None
    if not head or head[0].strip() != "---":
        return None
    slug = os.path.splitext(os.path.basename(path))[0]
    desc, kws = "", None
    for line in head[1:]:
        if line.strip() == "---":
            break
        m = KEYWORDS_RE.match(line)
        if m:
            kws = [k.strip().lower() for k in m.group(1).split(",") if k.strip()]
        elif line.startswith("description:"):
            desc = line.split(":", 1)[1].strip()
            if len(desc) >= 2 and desc[0] == desc[-1] and desc[0] in "\"'":
                desc = desc[1:-1]
    derived = not kws
    if derived:
        kws = []
        for w in _words(slug.replace("-", " ")) + _words(desc):
            if len(w) >= 4 and w not in KW_STOP and w not in kws:
                kws.append(w)
        kws = kws[:DERIVED_MAX]
    return {"slug": slug, "description": desc, "keywords": kws, "derived": derived}
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python test_guard.py 2>&1 | grep -E "kw-read" ; python test_guard.py >/dev/null 2>&1; echo rc=$?`
Expected: every `kw-read` line PASS, and `rc=0`.

- [ ] **Step 5: Commit**

```bash
git add guard.py test_guard.py
git commit -m "Memory keywords: the frontmatter reader, with a fallback from the summary"
```

---

### Task 2: The catalogue

**Files:**
- Modify: `guard.py`. Append to the new section, directly above `def cmd_bootstrap():`.
- Test: `test_guard.py`

- [ ] **Step 1: Write the failing tests**

```python
KW_MANIFEST = {"projects": {
    "droid": {"dir": "D:\\Demo\\Droid", "threads": ["droid"], "also": [], "files": []},
    "tides": {"dir": "D:\\Demo\\Tides", "threads": ["tides"], "also": [],
              "files": ["tide-tables"]}}}
DROID_KEY = "D--Demo-Droid"


def test_the_catalogue_prefers_the_shared_copy_and_names_each_project():
    """Spec section 2: one entry per slug, the shared copy wins over a spare, and the
    project comes from the manifest, then the one list that claims it, then 'general'."""
    home = kw_home(
        {(KEY, "android-adb"): mem_text("android-adb", "shared copy", ["adb"]),
         (DROID_KEY, "android-adb"): mem_text("android-adb", "SPARE copy", ["adb"]),
         (KEY, "tide-tables"): mem_text("tide-tables", "t", ["tide"]),
         (KEY, "loose-lesson"): mem_text("loose-lesson", "l", ["regex"])},
        lists={DROID_KEY: "# Memory Index\n- [Android adb](android-adb.md) - x\n"},
        manifest=KW_MANIFEST)
    try:
        r = kw_json(home, "import json\nprint(json.dumps({e['slug']: e for e in "
                          "guard.memory_catalogue()}))")
        a = r.get("android-adb") or {}
        check("kw-cat: one entry per slug", sorted(r) == ["android-adb", "loose-lesson",
                                                          "tide-tables"], repr(sorted(r)))
        check("kw-cat: the shared copy wins", a.get("description") == "shared copy", repr(a))
        check("kw-cat: title from the list line", a.get("title") == "Android adb", repr(a))
        check("kw-cat: project from the one list that claims it",
              a.get("project") == "droid", repr(a))
        check("kw-cat: project from the manifest's files",
              (r.get("tide-tables") or {}).get("project") == "tides", repr(r.get("tide-tables")))
        l = r.get("loose-lesson") or {}
        check("kw-cat: unclaimed -> general, title from the slug",
              l.get("project") == "general" and l.get("title") == "loose lesson", repr(l))
    finally:
        rm_tree(home)


def test_the_catalogue_cache_rereads_only_a_changed_file_and_survives_corruption():
    """Spec section 2: the prompt hook must not pay for every file on every prompt."""
    home = kw_home({(KEY, "one"): mem_text("one", "a", ["alpha"]),
                    (KEY, "two"): mem_text("two", "b", ["beta"])})
    count = ("import json\nn = []\nreal = guard.memory_keywords\n"
             "guard.memory_keywords = lambda p: (n.append(p), real(p))[1]\n"
             "guard.memory_catalogue()\nprint(json.dumps(len(n)))")
    try:
        check("kw-cache: a cold build reads every file", kw_json(home, count) == 2)
        check("kw-cache: a warm build reads none", kw_json(home, count) == 0)
        p = os.path.join(home, ".claude", "projects", KEY, "memory", "two.md")
        with open(p, "a", encoding="utf-8") as f:
            f.write("more\n")
        check("kw-cache: a changed file is re-read alone", kw_json(home, count) == 1)
        with open(os.path.join(home, ".claude", "context-guard", "memory-catalogue.json"),
                  "w", encoding="utf-8") as f:
            f.write("{not json")
        check("kw-cache: a corrupt cache is rebuilt", kw_json(home, count) == 2)
    finally:
        rm_tree(home)
```

Add both to the runner tuple.

- [ ] **Step 2: Run them and watch them fail**

Run: `python test_guard.py 2>&1 | grep -E "kw-cat|kw-cache" | head`
Expected: FAIL. The detail shows `_error` with `has no attribute 'memory_catalogue'`.

- [ ] **Step 3: Implement.** Append to the section:

```python
CATALOGUE = os.path.join(STATE, "memory-catalogue.json")
TITLE_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\.md\)")


def _memory_folders():
    """Every <project>/memory folder, the shared one first, so its copy of a slug wins over
    a spare that the one-home sweep has not tidied yet."""
    try:
        keys = sorted(os.listdir(PROJECTS))
    except Exception:
        return []
    shared = os.path.normcase(SHARED_MEMORY)
    out = [os.path.join(PROJECTS, k, "memory") for k in keys
           if os.path.isdir(os.path.join(PROJECTS, k, "memory"))]
    return sorted(out, key=lambda d: (os.path.normcase(d) != shared, d))


def _list_claims(folders):
    """slug -> its title in the first list that links it, and slug -> the keys of every
    folder whose MEMORY.md links it."""
    titles, claims = {}, {}
    for d in folders:
        key = os.path.basename(os.path.dirname(d))
        try:
            with open(os.path.join(d, "MEMORY.md"), encoding="utf-8-sig",
                      errors="replace") as f:
                text = f.read()
        except Exception:
            continue
        for title, s in TITLE_RE.findall(text):
            s = os.path.basename(s)
            titles.setdefault(s, title.strip())
            claims.setdefault(s, set()).add(key)
    return titles, claims


def _key_name(key, projects):
    for name, p in sorted(projects.items()):
        dirs = [p.get("dir") or ""] + list(p.get("also") or [])
        if any(dir_key(x) == key for x in dirs if x):
            return name
    return key


def _project_of(slug, folder, claims, projects):
    for name, p in sorted(projects.items()):
        if slug in (p.get("files") or []):
            return name
    keys = sorted(k for k in claims.get(slug, ()) if k != SOURCE_KEY)
    if len(keys) == 1:
        return _key_name(keys[0], projects)
    own = os.path.basename(os.path.dirname(folder))
    if own != SOURCE_KEY:
        return _key_name(own, projects)
    return "general"


def memory_catalogue():
    """Every memory on the machine, one entry per slug: slug, title, description, keywords,
    derived, project, path, mtime. Cached per file by path, mtime and size in CATALOGUE, so a
    prompt re-reads only what changed; a cache that does not parse is rebuilt, never trusted."""
    folders = _memory_folders()
    try:
        with open(CATALOGUE, encoding="utf-8") as f:
            cache = json.load(f)
        if not isinstance(cache, dict):
            cache = {}
    except Exception:
        cache = {}
    fresh, entries = {}, {}
    for d in folders:
        try:
            names = sorted(os.listdir(d))
        except Exception:
            continue
        for n in names:
            if not n.lower().endswith(".md") or n.lower() == "memory.md":
                continue
            slug = n[:-3]
            if slug in entries:
                continue
            p = os.path.join(d, n)
            try:
                s = os.stat(p)
            except Exception:
                continue
            sig = [s.st_mtime, s.st_size]
            c = cache.get(p)
            if isinstance(c, dict) and c.get("sig") == sig and "kw" in c:
                kw = c["kw"]
            else:
                kw = memory_keywords(p)
            fresh[p] = {"sig": sig, "kw": kw}
            if not isinstance(kw, dict):
                continue
            e = dict(kw)
            e.update({"path": p, "mtime": s.st_mtime, "folder": d})
            entries[slug] = e
    if fresh != cache:
        try:
            os.makedirs(STATE, exist_ok=True)
            tmp = CATALOGUE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(fresh, f)
            os.replace(tmp, CATALOGUE)
        except Exception as e:
            log("keywords: could not save the catalogue (%r)" % (e,))
    titles, claims = _list_claims(folders)
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            projects = (json.load(f) or {}).get("projects") or {}
    except Exception:
        projects = {}
    for slug, e in entries.items():
        e["title"] = titles.get(slug) or slug.replace("-", " ")
        e["project"] = _project_of(slug, e.pop("folder"), claims, projects)
    return list(entries.values())
```

- [ ] **Step 4: Run them and watch them pass**

Run: `python test_guard.py 2>&1 | grep -E "kw-cat|kw-cache"; python test_guard.py >/dev/null 2>&1; echo rc=$?`
Expected: all PASS, and `rc=0`.

- [ ] **Step 5: Commit**

```bash
git add guard.py test_guard.py
git commit -m "Memory keywords: a cached catalogue of every memory folder, each named by project"
```

---

### Task 3: The scorer and `--recall`

**Files:**
- Modify: `guard.py`. Append to the section. In the `if __name__ == "__main__":` dispatch, add `--recall` as the FIRST branch, because its words may contain other flags.
- Test: `test_guard.py`

- [ ] **Step 1: Write the failing tests**

```python
SCORE_MEMS = {
    (KEY, "adb-setup"): mem_text("adb-setup", "a", ["adb", "android phone"]),
    (KEY, "gradle-wsl"): mem_text("gradle-wsl", "g", ["gradle build", "android phone"]),
    (KEY, "emulator-notes"): mem_text("emulator-notes", "e", ["android phone", "emulator"]),
    (KEY, "telegram-bot-traps"): mem_text("telegram-bot-traps",
                                          "Telegram polling and webhook conflict"),
    (KEY, "bank-bill"): mem_text("bank-bill", "b", ["فاتورة"]),
}


def scored(home, text, strict=True):
    return kw_json(home, "import json\nprint(json.dumps([e['slug'] for _s, _m, e, _h in "
                         "guard.score_memories(%r, guard.memory_catalogue(), set(), %r)]))"
                   % (text, strict))


def test_the_scorer_asks_for_two_keywords_or_one_rare_one():
    """Spec section 3: a common keyword alone never qualifies, a rare one does, a phrase
    needs its words in order, derived keywords need two, and Arabic matches Arabic."""
    home = kw_home(SCORE_MEMS)
    try:
        check("kw-score: one common keyword is not enough",
              scored(home, "my android phone is slow") == [], repr(scored(home, "my android phone is slow")))
        check("kw-score: one rare keyword is enough",
              scored(home, "can adb see the device") == ["adb-setup"])
        check("kw-score: two keywords qualify",
              scored(home, "android phone and the emulator crashed") == ["emulator-notes"])
        check("kw-score: a phrase needs its words in order",
              scored(home, "phone android is slow", strict=False) == [])
        check("kw-score: a derived keyword alone is not enough",
              scored(home, "telegram is down again") == [])
        check("kw-score: two derived keywords qualify",
              scored(home, "telegram webhook broke again") == ["telegram-bot-traps"])
        check("kw-score: Arabic matches Arabic",
              scored(home, "اين فاتورة الشهر الماضي") == ["bank-bill"])
        check("kw-score: loose mode takes one common keyword",
              len(scored(home, "my android phone is slow", strict=False)) == 3)
    finally:
        rm_tree(home)


def run_recall(home, *words):
    env = child_env(home)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, GUARD, "--recall"] + list(words),
                          capture_output=True, text=True, encoding="utf-8", env=env)


def test_recall_lists_the_best_five_and_says_so_when_nothing_matches():
    """Spec section 4: the chat's own search - loose, top 5, title, project, path, keywords."""
    mems = {(KEY, "m%d" % i): mem_text("m%d" % i, "d", ["widget", "w%d" % i]) for i in range(7)}
    home = kw_home(mems)
    try:
        p = run_recall(home, "widget")
        expect_clean(p, "kw-recall")
        heads = [l for l in p.stdout.splitlines() if l and not l.startswith(" ")]
        check("kw-recall: at most five results", len(heads) == 5, p.stdout[:300])
        check("kw-recall: each result names its path and keywords",
              ".md" in heads[0] and "  keywords: widget" in p.stdout, p.stdout[:300])
        q = run_recall(home, "nothing", "here")
        check("kw-recall: the empty case says so in one line",
              q.stdout.strip() == "No memory matches: nothing here", q.stdout)
    finally:
        rm_tree(home)
```

Add both to the runner tuple.

- [ ] **Step 2: Run them and watch them fail**

Run: `python test_guard.py 2>&1 | grep -E "kw-score|kw-recall" | head`
Expected: FAIL (`score_memories` is missing, and `--recall` prints nothing).

- [ ] **Step 3: Implement.** Append to the section:

```python
RARE_MAX = 2        # a keyword carried by this many memories or fewer is rare
RECALL_TOP = 5


def _has_phrase(words, phrase):
    n = len(phrase)
    return n > 0 and any(words[i:i + n] == phrase for i in range(len(words) - n + 1))


def score_memories(text, catalogue, exclude=(), strict=True):
    """[(score, mtime, entry, hits)], best first. score = distinct keywords whose words all
    appear in `text`, in order. strict (the prompt hook) asks for two, or one RARE keyword of
    3+ characters, and always two when the keywords are derived; loose (--recall, which the
    chat asked for on purpose) takes one."""
    words = _words(text)
    if not words:
        return []
    carried = {}
    for e in catalogue:
        if not e.get("derived"):
            for k in set(e.get("keywords") or []):
                carried[k] = carried.get(k, 0) + 1
    out = []
    for e in catalogue:
        if e.get("slug") in exclude:
            continue
        hits = [k for k in dict.fromkeys(e.get("keywords") or [])
                if _has_phrase(words, _words(k))]
        if not hits:
            continue
        if strict and len(hits) < 2:
            if e.get("derived"):
                continue
            if not any(carried.get(k, 0) <= RARE_MAX and len(k) >= 3 for k in hits):
                continue
        out.append((len(hits), e.get("mtime") or 0, e, hits))
    out.sort(key=lambda t: (-t[0], -t[1]))
    return out


def cmd_recall():
    """--recall <words>: the chat's own search, for the moment mid-task when something comes
    up that his message never named. Prints; reads no stdin; exits 0 either way."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    a = sys.argv[1:]
    words = " ".join(a[a.index("--recall") + 1:]).strip()
    if not words:
        print("--recall needs some words to search for.")
        return
    try:
        res = score_memories(words, memory_catalogue(), strict=False)[:RECALL_TOP]
    except Exception as e:
        log("recall: failed (%r)" % (e,))
        res = []
    if not res:
        print("No memory matches: " + words)
        return
    for _s, _m, e, _h in res:
        print("%s (%s) - %s" % (e["title"], e["project"], e["path"]))
        print("  keywords: " + ", ".join(e.get("keywords") or []))
```

The dispatch change: `if "--archived" in a:` becomes

```python
    if "--recall" in a:
        cmd_recall()
    elif "--archived" in a:
```

- [ ] **Step 4: Run them and watch them pass**

Run: `python test_guard.py 2>&1 | grep -E "kw-score|kw-recall"; python test_guard.py >/dev/null 2>&1; echo rc=$?`
Expected: all PASS, and `rc=0`.

- [ ] **Step 5: Commit**

```bash
git add guard.py test_guard.py
git commit -m "Memory keywords: one scorer, and --recall for the chat's own search"
```

---

### Task 4: The automatic hint

**Files:**
- Modify: `guard.py`:
  - append `memory_hints` to the section;
  - in `cmd_size`, after `out = archive_step(out, d, sid)`, add `out = memory_hints(out, d, sid)`;
  - at the pickup call site, after `hand += label_memory_index(d.get("prompt") or "")`, record the fetched list;
  - add `label_list_path` beside `label_memory_index`.
- Test: `test_guard.py`

- [ ] **Step 1: Write the failing tests**

```python
HINT_MEMS = {
    (KEY, "android-adb"): mem_text("android-adb", "adb on the phone", ["adb", "usb debugging"]),
    (KEY, "own-thing"): mem_text("own-thing", "o", ["kettle", "teapot"]),
    (KEY, "rule-thing"): mem_text("rule-thing", "r", ["spoon", "fork"]),
    (KEY, "label-thing"): mem_text("label-thing", "l", ["anchor", "rope"]),
}
HINT_LISTS = {KEY: "# Memory Index\n- [Own](own-thing.md) - o\n",
              DROID_KEY: "# Memory Index\n- [Android adb](android-adb.md) - x\n"}


def test_the_hint_names_another_projects_memory_once():
    """Spec section 3, the whole path: his prompt names it, the chat gets one line with
    the project and the path, the search tip rides the first hint only, never twice."""
    home = kw_home(HINT_MEMS, lists=HINT_LISTS, manifest=KW_MANIFEST)
    try:
        p = run(home, "hint1", "why does adb not see my phone today")
        expect_clean(p, "kw-hint")
        c = context_of(p)
        check("kw-hint: the matching memory is named with its project",
              "Android adb (droid)" in c and "android-adb.md" in c, c[:400])
        check("kw-hint: the first hint carries the search tip", "--recall" in c, c[:400])
        check("kw-hint: within the cap", len(c) <= guard_constant("HINT_CHARS"), str(len(c)))
        q = run(home, "hint1", "adb still does not see my phone")
        check("kw-hint: the same memory is never hinted twice",
              "android-adb.md" not in context_of(q), context_of(q)[:300])
    finally:
        rm_tree(home)


def test_the_hint_skips_what_the_chat_already_has():
    """The chat's own list, CLAUDE.md's [[rules]] and the label-fetched list are already in
    front of it; hinting them is the bloat this tool exists to remove."""
    home = kw_home(HINT_MEMS, lists=HINT_LISTS, manifest=KW_MANIFEST,
                   claude_md="- a rule `[[rule-thing]]`\n")
    try:
        lst = os.path.join(home, "label-list.md")   # outside every memory folder
        with open(lst, "w", encoding="utf-8") as f:
            f.write("- [L](label-thing.md) - l\n")
        guard_call(home, "guard.save_state('hint2', {'label_list': %r})" % lst)
        for words, what in (("the kettle and the teapot again", "own list"),
                            ("a spoon and a fork please now", "CLAUDE.md rule"),
                            ("the anchor and the rope today", "label list")):
            c = context_of(run(home, "hint2", words))
            check("kw-hint-skip: " + what, "MEMORIES FROM OTHER PROJECTS" not in c, c[:200])
    finally:
        rm_tree(home)


def test_the_hint_stays_quiet_when_it_should():
    """A short prompt, the off switch and a busy turn get nothing, and nothing is recorded."""
    home = kw_home(HINT_MEMS, lists=HINT_LISTS, manifest=KW_MANIFEST)
    try:
        check("kw-hint-quiet: three words or fewer",
              "android-adb" not in context_of(run(home, "hint3", "adb help now")))
        r = kw_json(home, "import json\n"
                    "o = {'hookSpecificOutput': {'additionalContext': 'busy'}}\n"
                    "d = {'session_id': 'hint3', 'prompt': 'why does adb not see my phone'}\n"
                    "r = guard.memory_hints(o, d, 'hint3')\n"
                    "print(json.dumps([r['hookSpecificOutput']['additionalContext'],\n"
                    "  guard.load_state('hint3').get('memory_hints')]))")
        check("kw-hint-quiet: a busy turn is returned unchanged and nothing recorded",
              r == ["busy", None], repr(r))
        open(os.path.join(home, ".claude", "context-guard", "no-memory-hints"), "w").close()
        check("kw-hint-quiet: the off switch",
              "android-adb" not in context_of(run(home, "hint4", "why does adb not see my phone")))
    finally:
        rm_tree(home)


def test_the_hint_shows_at_most_three():
    mems = {(KEY, "w%d" % i): mem_text("w%d" % i, "d" * 120, ["sprocket", "flange"])
            for i in range(6)}
    home = kw_home(mems)
    try:
        c = context_of(run(home, "hint5", "the sprocket and the flange broke"))
        n = sum(1 for l in c.splitlines() if l.startswith("- "))
        check("kw-hint-cap: at most three memories", 1 <= n <= 3, c[:400])
        check("kw-hint-cap: under the character cap",
              len(c) <= guard_constant("HINT_CHARS"), str(len(c)))
    finally:
        rm_tree(home)
```

Add all four to the runner tuple.

- [ ] **Step 2: Run them and watch them fail**

Run: `python test_guard.py 2>&1 | grep -E "kw-hint" | head`
Expected: FAIL, because no hint is produced.

- [ ] **Step 3: Implement.** Append to the section:

```python
HINTS_OFF = "no-memory-hints"
HINT_MAX = 3
HINT_CHARS = 900
HINT_DESC = 140
HINT_MIN_WORDS = 4
CLAUDE_MD = os.path.join(HOME, ".claude", "CLAUDE.md")
WIKI_RE = re.compile(r"\[\[([^\]\s]+)\]\]")


def _linked_slugs(path):
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
    except Exception:
        return set()
    return {os.path.basename(s) for _t, s in TITLE_RE.findall(text)}


def memory_hints(out, d, sid):
    """Merge, into the prompt hook's ONE json object, a line for each memory from ANOTHER
    project that his message matches - titles only, at most HINT_MAX, never the same one
    twice in a chat. Never raises, never prints."""
    try:
        return _memory_hints(out, d, sid)
    except Exception as e:
        log("hints: failed (%r)" % (e,))
        return out


def _memory_hints(out, d, sid):
    if not sid or os.path.exists(os.path.join(STATE, HINTS_OFF)):
        return out
    hso0 = (out or {}).get("hookSpecificOutput")
    if isinstance(hso0, dict) and hso0.get("additionalContext"):
        return out          # a pickup, menu, warning or archive offer: the app cuts the tail
                            # of a long injection, so the hint waits for an ordinary prompt
    prompt = d.get("prompt") or ""
    if ledger_noise(prompt) or len(_words(prompt)) < HINT_MIN_WORDS:
        return out
    path = find_transcript(sid, d.get("transcript_path")) or virtual_transcript(d, sid)
    st = load_state(sid)
    shown = list(st.get("memory_hints") or [])
    exclude = set(shown) | _linked_slugs(os.path.join(memory_dir(path), "MEMORY.md"))
    if st.get("label_list"):
        exclude |= _linked_slugs(st["label_list"])
    try:
        with open(CLAUDE_MD, encoding="utf-8-sig", errors="replace") as f:
            exclude |= set(WIKI_RE.findall(f.read()))
    except Exception:
        pass
    res = score_memories(prompt, memory_catalogue(), exclude, strict=True)[:HINT_MAX]
    if not res:
        return out
    lines = ["MEMORIES FROM OTHER PROJECTS that match his message. Titles only: open a file "
             "only if it helps with what he just asked, and say nothing about this list "
             "otherwise."]
    tail = [] if shown else [
        "To search every memory yourself mid-task: python \"%s\" --recall <words>"
        % os.path.abspath(__file__)]
    used = []
    for _s, _m, e, _h in res:
        desc = e.get("description") or ""
        if len(desc) > HINT_DESC:
            desc = desc[:HINT_DESC - 3].rstrip() + "..."
        line = "- %s (%s) - %s: %s" % (e["title"], e["project"], desc, e["path"])
        if len(chr(10).join(lines + [line] + tail)) > HINT_CHARS:
            break
        lines.append(line)
        used.append(e["slug"])
    if not used:
        return out
    st["memory_hints"] = shown + used
    save_state(sid, st)
    out = out or {}
    hso = out.get("hookSpecificOutput")
    if not isinstance(hso, dict):
        hso = {}
        out["hookSpecificOutput"] = hso
    hso["hookEventName"] = "UserPromptSubmit"
    hso["additionalContext"] = chr(10).join(lines + tail)
    log("hints: %d memory hint(s): %s" % (len(used), ", ".join(used)))
    return out
```

Beside `label_memory_index`, directly above `def label_memory_index(prompt):`:

```python
def label_list_path(prompt):
    """The project list label_memory_index() fetches for this label, or None."""
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            man = json.load(f)
    except Exception:
        return None
    _name, entry = thread_project(prompt, man)
    if not entry:
        return None
    return os.path.join(PROJECTS, dir_key(entry.get("dir") or ""), "memory", "MEMORY.md")
```

At the pickup call site. The anchor is the line `            hand += label_memory_index(d.get("prompt") or "")`. Insert after it:

```python
            lp = label_list_path(d.get("prompt") or "")
            if lp and os.path.exists(lp):
                st["label_list"] = lp     # memory_hints must not re-offer what this put
                save_state(sid, st)       # in front of the chat
```

In `cmd_size`, the anchor is `    out = archive_step(out, d, sid)\r\n`. Insert after it:

```python
    out = memory_hints(out, d, sid)
```

- [ ] **Step 4: Run them and watch them pass**

Run: `python test_guard.py 2>&1 | grep -E "kw-hint"; python test_guard.py >/dev/null 2>&1; echo rc=$?`
Expected: all PASS, and `rc=0`. Every older test must stay green: no older fixture has `keywords` lines, and the derived fallback needs two hits.

- [ ] **Step 5: Commit**

```bash
git add guard.py test_guard.py
git commit -m "Memory keywords: the prompt hook names a matching memory from another project"
```

---

### Task 5: Keeping keywords written

**Files:**
- Modify: `guard.py`:
  - append `memory_files_written` and `keywords_block` to the section;
  - in `cmd_ledger`, insert the call between the note-head check and `memory_nag`;
  - add one sentence each to `memory_nag_text` and `label_memory_index`.
- Test: `test_guard.py`

- [ ] **Step 1: Write the failing tests**

```python
def write_mem_writes(home, sid, paths, tool="Write", mention=()):
    """A transcript in which this chat used `tool` on each of `paths`, and only MENTIONED
    each of `mention` in a message."""
    p = os.path.join(home, ".claude", "projects", KEY, sid + ".jsonl")
    started = time.time() - 600
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "timestamp": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
            "message": {"role": "user", "content": "save it " + " ".join(mention)}}) + chr(10))
        for fp in paths:
            f.write(json.dumps({"type": "assistant", "message": {"role": "assistant",
                    "content": [{"type": "tool_use", "name": tool,
                                 "input": {"file_path": fp, "content": "x"}}]}}) + chr(10))
    return p


def test_a_memory_saved_without_keywords_is_sent_back_once():
    """Spec section 5: Claude Code's own memory format has no keywords field, so asking is
    not enough - the Stop hook checks what this chat actually wrote."""
    home = kw_home({(KEY, "bare-one"): mem_text("bare-one", "no keywords here"),
                    (KEY, "good-one"): mem_text("good-one", "fine", ["alpha", "beta"]),
                    (KEY, "said-only"): mem_text("said-only", "only mentioned")})
    try:
        md = os.path.join(home, ".claude", "projects", KEY, "memory")
        write_mem_writes(home, "kwstop", [os.path.join(md, "bare-one.md"),
                                          os.path.join(md, "good-one.md"),
                                          os.path.join(md, "MEMORY.md")],
                         mention=[os.path.join(md, "said-only.md")])
        p = run_stop(home, "kwstop")
        expect_clean(p, "kw-stop")
        why = blocked(p)
        check("kw-stop: blocked", why != "", (p.stdout or "")[:200])
        check("kw-stop: names the bare memory", "bare-one.md" in why, why[:300])
        check("kw-stop: not the good one, MEMORY.md or a mere mention",
              "good-one.md" not in why and "MEMORY.md" not in why and "said-only" not in why,
              why[:300])
        check("kw-stop: names its off switch", "no-keywords-check" in why, why[-200:])
        check("kw-stop: never twice in a row", blocked(run_stop(home, "kwstop", active=True)) == "")
        check("kw-stop: never twice for the same files", blocked(run_stop(home, "kwstop")) == "")
    finally:
        rm_tree(home)


def test_the_keywords_check_has_an_off_switch_and_a_cap():
    home = kw_home({(KEY, "bare-two"): mem_text("bare-two", "x")})
    try:
        md = os.path.join(home, ".claude", "projects", KEY, "memory")
        bare = os.path.join(md, "bare-two.md")
        write_mem_writes(home, "kwcap", [bare], tool="Edit")
        n = 0
        for i in range(4):
            os.utime(bare, (time.time() + i + 1, time.time() + i + 1))  # a new version each time
            n += 1 if blocked(run_stop(home, "kwcap")) else 0
        check("kw-stop-cap: at most NAG_MAX blocks", n == guard_constant("NAG_MAX"), str(n))
        write_mem_writes(home, "kwoff", [bare])
        open(os.path.join(home, ".claude", "context-guard", "no-keywords-check"), "w").close()
        check("kw-stop-off: the switch", blocked(run_stop(home, "kwoff")) == "")
    finally:
        rm_tree(home)


def test_the_saving_instructions_ask_for_keywords():
    home = kw_home({}, manifest={"projects": {"droid": {"dir": "D:\\Demo\\Droid",
                                                         "threads": ["droid"], "files": []}}},
                   lists={DROID_KEY: "# Memory Index\n- [A](a.md) - a\n"})
    try:
        t = kw_json(home, "import json\nprint(json.dumps(guard.label_memory_index('droid -2 (1 Oct)')))")
        check("kw-ask: the label fetch asks for keywords", "keywords:" in (t or ""), repr(t)[:300])
        started = time.time() - 3600
        write_chat(home, "kwnag", started)
        write_note(home, "kwnag")
        check("kw-ask: the memory nag asks for keywords",
              "keywords:" in blocked(run_stop(home, "kwnag")))
    finally:
        rm_tree(home)
```

Add all three to the runner tuple.

- [ ] **Step 2: Run them and watch them fail**

Run: `python test_guard.py 2>&1 | grep -E "kw-stop|kw-ask" | head`
Expected: FAIL.

- [ ] **Step 3: Implement.** Append to the section:

```python
KEYWORDS_OFF = "no-keywords-check"


def memory_files_written(transcript_path):
    """Memory TOPIC files this chat wrote with Write/Edit, in any project's memory folder.
    Positional, like chat_wrote_memory: a path that is only mentioned - in his message, in a
    hook's own text - is not a write."""
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except Exception:
        return []
    root = os.path.normcase(os.path.abspath(PROJECTS))
    out = []
    for line in lines:
        if "tool_use" not in line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        msg = rec.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        for block in (content if isinstance(content, list) else []):
            if (not isinstance(block, dict) or block.get("type") != "tool_use"
                    or block.get("name") not in ("Write", "Edit", "MultiEdit")):
                continue
            fp = (block.get("input") or {}).get("file_path") \
                if isinstance(block.get("input"), dict) else None
            if not isinstance(fp, str) or not fp.lower().endswith(".md"):
                continue
            n = os.path.normcase(os.path.abspath(fp))
            folder = os.path.dirname(n)
            if (os.path.basename(folder) == os.path.normcase("memory")
                    and os.path.dirname(os.path.dirname(folder)) == root
                    and os.path.basename(n).lower() != "memory.md" and fp not in out):
                out.append(fp)
    return out


def keywords_block(d, path):
    """A memory this chat saved has no keywords line. Claude Code's own memory format has no
    such field, so the saving instructions can be read and skipped; this reads what was
    actually written. Same brakes as note_head_block. Never carries the memory nag: it only
    fires when this chat wrote a memory, and that already keeps the nag quiet. Returns True
    when it blocked, so the caller prints exactly one decision."""
    if d.get("stop_hook_active") or os.path.exists(os.path.join(STATE, KEYWORDS_OFF)):
        return False
    sid = d.get("session_id")
    if not sid:
        return False
    missing, sig = [], []
    for fp in memory_files_written(path):
        if not os.path.isfile(fp):
            continue
        kw = memory_keywords(fp)
        if kw is None or not kw["derived"]:
            continue        # not a memory file, or it already has its line
        missing.append(fp)
        sig.append([fp, os.path.getmtime(fp)])
    if not missing:
        return False
    st = load_state(sid)
    if st.get("kw_nag_sig") == sig:
        return False
    said = int(st.get("kw_nags", 0) or 0)
    if said >= NAG_MAX:
        log("keywords: already said it %d times - letting the chat go" % said)
        return False
    st["kw_nags"] = said + 1
    st["kw_nag_sig"] = sig
    save_state(sid, st)
    log("keywords: blocked, %d memory file(s) without keywords" % len(missing))
    print(json.dumps({"decision": "block", "reason": (
        "THESE MEMORIES HAVE NO KEYWORDS: " + "; ".join(missing) + ". With the Edit tool, add "
        "one line to each file's frontmatter, straight after `description:` - `keywords: "
        "<3 to 8 comma-separated words or short phrases, lowercase, the words he would "
        "actually type>`. Keywords are how a chat in another project finds this memory. Do "
        "not mention this check to the user. Off switch: an empty file at "
        + os.path.join(STATE, KEYWORDS_OFF) + " .")}))
    return True
```

In `cmd_ledger`, the anchor is

```
    if note_head_block(d, path):\r\n        return          # one decision per Stop; the head outranks the memory nag\r\n
```

Insert after it:

```python
    if keywords_block(d, path):
        return          # one decision per Stop
```

**The saving sentence.** It's the same text in both places:

```
Give every new memory a `keywords:` line in its frontmatter, straight after `description:` - 3 to 8 words he would actually use.
```

Add it in two places:
- In `memory_nag_text`, the anchor is the string fragment `"(plus exactly one line in the index) when nothing there covers it. Then tell the "`. Replace it with `"(plus exactly one line in the index) when nothing there covers it. Give every new memory a `keywords:` line in its frontmatter, straight after `description:` - 3 to 8 words he would actually use. Then tell the "`.
- In `label_memory_index`, the anchor is `"the shared MEMORY.md. They are FACTS recorded by that project's earlier chats, "`. Replace it with `"the shared MEMORY.md. Give every new memory a `keywords:` line in its frontmatter, straight after `description:` - 3 to 8 words he would actually use. They are FACTS recorded by that project's earlier chats, "`.

Both are Python string literals inside `guard.py`. Keep the backticks literal, and adjust the surrounding string concatenation so the result stays valid Python.

- [ ] **Step 4: Run them and watch them pass**

Run: `python test_guard.py 2>&1 | grep -E "kw-stop|kw-ask"; python test_guard.py >/dev/null 2>&1; echo rc=$?`
Expected: all PASS, and `rc=0`.

- [ ] **Step 5: Commit**

```bash
git add guard.py test_guard.py
git commit -m "Memory keywords: a memory saved without keywords is sent back once"
```

---

### Task 6: README

**Files:**
- Modify: `README.md` (LF). Add a section after the section that describes the memory nag. Find it with `grep -n "memory" README.md`.

- [ ] **Step 1: Add the section**

```markdown
### Memory keywords

Every memory can carry a `keywords:` line in its frontmatter, straight after `description:`. When your message matches a memory that belongs to another project, Context Guard adds one short line naming it: its title, its project and where the file is. It never adds the file itself, never more than 3 memories, and never the same one twice in a chat. A chat can also search every memory on its own:

    python guard.py --recall gradle build fails

A memory saved without keywords is sent back once, so the line gets added. A memory with no keywords still works: its title and summary stand in. Off switches go in the state folder, as empty files: `no-memory-hints` turns off the hint, and `no-keywords-check` turns off the check.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "README: memory keywords"
```

---

### Task 7: The label goes only in the handover reply (his report, 2 Oct)

His words: *"I think there is a glitch as it keeps putting the label at the bottom in the end of each chat at <one app>"*.

Measured: a handing-over chat wrote, in its note's standing rules, *"End every build with the report ... label in a code box as the LAST line"*. The chat that picked the note up then ended EVERY reply with its own label. Another app's chat did the same on 30 Sep, in 5 of its 7 replies.

**Files:**
- Modify: `guard.py` (two string literals)
- Test: `test_guard.py`

- [ ] **Step 1: Write the failing test**

```python
def test_the_label_belongs_only_to_the_handover_reply():
    """2 Oct 2026: a note said 'label in a code box as the LAST line' and the next chat ended
    every reply with its own label. Both ends are told: the writer how to phrase the rule,
    the reader never to end a reply with its own label."""
    home = make_home({KEY + ".lab12345.md": "HANDOFF LABEL: Harbor -4 (2 Oct)\n"
                      "WRITTEN BY: Harbor -3 (1 Oct)\nNEXT CHAT EFFORT: high - x\n\nbody\n"})
    try:
        steps = guard_call(home, "print(guard.handoff_steps('2 Oct'))").stdout
        check("label-only: the writer is told it is the handover reply only",
              "HANDOVER reply only" in steps, steps[:300])
        c = context_of(run(home, "labchat1", "Harbor -4 (2 Oct)"))
        check("label-only: the pickup says never end a reply with its own label",
              "Never end a reply with this chat's own label" in c, c[:600])
    finally:
        rm_tree(home)
```

Add it to the runner tuple.

- [ ] **Step 2: Run it and watch it fail.** Both checks FAIL.

- [ ] **Step 3: Implement.** Two string-literal edits in `guard.py`, done with the CRLF-safe patch script. Each anchor must occur exactly once.

In `handoff_steps`, the anchor is

```
"very last line of the reply - nothing after it, not a table, not a sign-off. His words, "
```

Replace it with

```
"very last line of the reply - nothing after it, not a table, not a sign-off. That is THIS "
        "handover reply only: an ordinary reply never ends with a label. In the note you write, "
        "phrase the rule as 'label as the last line of a HANDOVER reply only' - a note that said "
        "'label as the LAST line' made the next chat end every reply with its own label "
        "(measured 2 Oct 2026). His words, "
```

In `pending_handoff`, the anchor is

```
"picked up where things left off. FIRST ACTION, before anything else: rename "
```

Replace it with

```
"picked up where things left off. Never end a reply with this chat's own label: a "
            "label is given only in the one reply that hands over to the NEXT chat, whatever "
            "the note's standing rules seem to say. FIRST ACTION, before anything else: rename "
```

The continuation lines must keep the indentation of the string they sit in, and must end in `\r\n`.

- [ ] **Step 4: Run it and watch it pass.** Run the full file: `rc=0`.

- [ ] **Step 5: Commit**

```bash
git add guard.py test_guard.py
git commit -m "A label belongs only to the handover reply, never to every reply"
```

---

## After the build (controller, not an implementer)

1. Review every commit with `cg-reviewer`, then run the full suite. Then push.
2. Run the one-time fill (spec section 6):
   1. back up the shared folder;
   2. turn the drafts in `keywords.json` into a review page for the author;
   3. apply after his OK, with the line-ending-preserving script;
   4. prove it with `--recall adb` and a cold-build timing.
3. Add the `--recall` standing line to `~/.claude/CLAUDE.md`.
