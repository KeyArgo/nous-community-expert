# Talio v5 — Max-Gamification Plan

> **For Hermes:** Execute task-by-task. Each task ends with a commit. 6h auto-deploy via Cloudflare Pages is already proven. v4 plan lives at `.hermes/plans/2026-06-09_130000-talio-v4.md` — this is a follow-on plan that reuses the v4 build pipeline.

**Goal:** Take Talio from "has medals" to "Steam-level rankings + 30 badges + hidden achievements + Gilded mastery + profile showcase" while respecting the static-site architecture, the 6h rebuild cadence, and the power-law distribution of 6,177 authors (most with 1 message).

**Why now:** User asked to "gamify this to the max, Steam-style rankings, whole badging system, achievement system." A deepthink huddle with a naysayer seat surfaced 3 hard pushbacks that are baked into the v5 design:

1. **Power-law risk.** ~50% of authors have 1 chunk. A "max it out" tier system would make 3,000 people feel like "Level 1, 0 badges" — actively hostile. v5 handles this with (a) a generous default Tier 1 (Apprentice, visually appealing, not ashamed), (b) keeping the existing Ribbon for everyone, (c) hidden badges that even 1-message users can earn, (d) Gilded requiring *multi-domain* not just volume.
2. **"Max measurement vs max gamification."** No login, no server, no live signals. The "max" comes from *depth* (richer taxonomy, profile showcase, progress bars) not from live social feed.
3. **Static-architecture irony.** Rebuilds every 6h. Steam's "X just unlocked Y" live feed is impossible. v5 uses *build-snapshot diff* (a `prev_stats.json` in repo) for a "Since Last Watch" feed. v1 defers this to v2; v5 ships without it.

**Architecture:** Same as v4. `build_index.py` does derivations, serializes to JSON. `index.html` renders pills + profile page. No server, no API, no new deps.

**Repo:** `/mnt/homes/galileo/argo/Development/nous-community-expert-dev/`

**v4 → v5 delta:**
1. Uncap the `awards[:2]` cap in `assign_awards()` — single line, instant 5x visible badges
2. New **rank tier system** (10 tiers, Greek-themed) computed from chunks + active days
3. Expanded **badge taxonomy** to ~28 badges across 5 categories (Longevity, Breadth, Quality, Hidden, Milestone)
4. **Hidden badges** (4 of them) with grayscale + reveal-on-hover CSS
5. **Gilded** rarity tier (multi-domain mastery, replaces "Foil")
6. **Progress bars** for near-miss tiered badges (e.g. "30/50 Active Days")
7. **Profile page redesign** — Steam-style: rank, featured showcase, full grid, progress, rivals (neighbors on leaderboard), recent activity
8. Expanded **badge legend modal** with all new badges + rarities
9. Carry-over from v4: data fields, central `renderBadges`, OG tags, intro, qa script — all preserved

**Huddle constraints (carried from v4 + naysayer):**
- Long-tail must feel included, not shamed — every profile should have at least the Ribbon + Apprentice title
- Badge thresholds must be testable from existing data only — no ML, no embeddings
- Gilded requires cross-domain (not just volume) — promotes well-rounded participation
- All derivations in `build_index.py` (O(authors × chunks) max), all rendering in `index.html`
- Static budget: per-file < 25 MB Cloudflare limit (current: 16 MB / 13 MB — plenty of headroom)
- v1 ships in a few hours; v2/v3 deferred to backlog at the bottom

**Measured constraints (verified against the live data, 2026-06-09):**
- search-data.json: 16 MB (per-file Cloudflare Pages limit: 25 MB → **~9 MB headroom**)
- search-index.json: 13 MB
- Total ~29 MB; v4 plan had this as 15 MB — archive has grown by ~1 MB since then
- 6,177 authors, 19,180 chunks, 291,939 messages
- 158 distinct archive days (2024-12-10 → 2026-06-06)
- v5 adds (per-user `tier`, expanded `awards`, `progress`, `gilded`, `rank_percentile`): ~50–80 KB
- Real constraint is **per-file < 25 MB** (Cloudflare Pages), not total. Both files well under.

**Workspace:** `/mnt/homes/galileo/argo/Development/nous-community-expert-dev/`

---

## T0: QA Scaffolding (already exists from v4 — verify still green)

`qa.py` already validates JSON shape from v4. **Skip rebuild** — re-run existing script to confirm baseline:

```bash
cd /mnt/homes/galileo/argo/Development/nous-community-expert-dev
python3 qa.py
```

If it returns non-zero, fix before proceeding. Otherwise jump to T1.

---

## T1: Uncap Awards + Expand Badge Taxonomy (the data layer)

**Files:** `build_index.py:151-173` (function `assign_awards`)

**Change 1a:** Remove the `awards[:2]` cap.

```python
# build_index.py — replace the last line of assign_awards
-    return awards[:2]
+    return awards
```

**Change 1b:** Expand the badge catalog. Replace the function body with the v5 taxonomy. Each badge is `[id, name, category, tier]`. Categories: `longevity`, `breadth`, `quality`, `hidden`, `milestone`. Tiers: `bronze` (default), `silver` (top 10% of holders), `gold` (top 1%), `gilded` (multi-domain — handled in T4).

```python
def assign_awards(user_stats):
    """Stable contribution awards from lifetime stats. Multi-badge now."""
    awards = []
    s = user_stats  # alias for readability

    # ---- LONGEVITY (5 badges) ----
    if s.get("active_days", 0) >= 10:   awards.append(["settler",   "Settler",   "longevity", "bronze"])
    if s.get("active_days", 0) >= 50:   awards.append(["pillar",    "Pillar",    "longevity", "silver"])
    if s.get("active_days", 0) >= 100:  awards.append(["centurion", "Centurion", "longevity", "gold"])
    if s.get("active_days", 0) >= 250:  awards.append(["vanguard",  "Vanguard",  "longevity", "gold"])
    if s.get("active_days", 0) >= 500:  awards.append(["immortal",  "Immortal",  "longevity", "gilded"])

    if s.get("max_streak", 0) >= 7:     awards.append(["spark",   "Spark",   "longevity", "bronze"])
    if s.get("max_streak", 0) >= 30:    awards.append(["streak",  "Streak",  "longevity", "silver"])
    if s.get("max_streak", 0) >= 90:    awards.append(["kindled", "Kindled", "longevity", "gold"])
    if s.get("max_streak", 0) >= 180:   awards.append(["inferno", "Inferno", "longevity", "gilded"])

    # ---- BREADTH (5 badges) ----
    chans = len(s.get("channels", set()) or set())
    if chans >= 2:                       awards.append(["wanderer",  "Wanderer",  "breadth", "bronze"])
    if chans >= 3:                       awards.append(["explorer",  "Explorer",  "breadth", "silver"])
    if chans >= 4:                       awards.append(["diplomat",  "Diplomat",  "breadth", "gold"])
    if chans >= 4 and s.get("first_seen","9999") <= "2025-01-01":
                                          awards.append(["pioneer",   "Pioneer",   "breadth", "gilded"])

    # Socialite: active in ALL 4 channels on the same day
    if s.get("same_day_all_channels", False):
                                          awards.append(["socialite", "Socialite", "breadth", "gilded"])

    # ---- QUALITY (3 badges) ----
    if s.get("msg_per_chunk", 0) > 15:   awards.append(["sage",        "Sage",        "quality", "silver"])
    if s.get("msg_per_chunk", 0) > 30:   awards.append(["elocutionist","Elocutionist","quality", "gold"])
    if s.get("long_thread_starter", False):
                                          awards.append(["catalyst",    "Catalyst",    "quality", "gilded"])

    # ---- MILESTONE (5 badges — tied to rank tier, computed in T2) ----
    # Filled in T2 after tier system lands. Placeholder list:
    # ["apprentice","disciple","philosopher","architect","artificer"]
    # will be appended to awards in T2.

    # ---- HIDDEN (4 badges — see T3) ----
    # Filled in T3. Hidden: tech_priest, night_owl, one_percent, hermes_herald.

    return awards
```

**Change 1c:** Pre-compute the new stats. In the per-user loop (around `build_index.py:255-272`), add:

```python
# After max_streak is computed, also compute:
_s["same_day_all_channels"] = False
_days_to_channels = {}
for _d in _s["dates_active"]:
    pass  # we'll re-derive below using author_dates

# In the existing per-chunk loop above, also track:
if chunk_date not in _days_to_channels:
    _days_to_channels[chunk_date] = set()
_days_to_channels[chunk_date].add(ch)
# After the loop:
_s["same_day_all_channels"] = any(
    len(v) >= 4 for v in _days_to_channels.values()
)

# Track long-thread-starter: chunks where the user is the first author AND len(messages) >= 30
_s["long_thread_starter"] = False
# Add this inside the per-chunk author loop:
# (After we know this is the first author in chunk.messages by message timestamp,
#  AND len(c.get("messages", [])) >= 30, set _s["long_thread_starter"] = True)
```

**Change 1d:** Add the new fields to the serialized leaderboard entry (around `build_index.py:280`):

```python
leaderboard.append({
    "name": author, "total_chunks": tc,
    "total_messages": s["total_messages"],
    "channels_active": len(s["channels"]),
    "first_seen": s["first_seen"][:10] if s["first_seen"] else "",
    "last_seen": s["last_seen"][:10] if s["last_seen"] else "",
    "max_streak": s["max_streak"],
    "msg_per_chunk": round(s["msg_per_chunk"], 2),
    "active_days": len(s["dates_active"]),
    "medal": medal,
    "awards": awards,                  # <-- now uncapped
    "tier": None,                      # <-- filled in T2
    "gilded": [],                      # <-- filled in T4
    "progress": {                      # <-- filled in T5: near-miss stats
        "next_tier": None,             # {name, current, target, metric}
        "next_longevity": None,        # {target, current, badge}
        "next_streak": None,           # {target, current, badge}
    },
    "hidden_seen": [],                 # <-- filled in T3
})
```

**Verification:**
```bash
cd /mnt/homes/galileo/argo/Development/nous-community-expert-dev
python3 build_index.py 2>&1 | tail -5
python3 -c "import json; d=json.load(open('search-data.json')); u=d['leaderboard'][0]; print('awards count:', len(u['awards']), 'tier:', u['tier'])"
# Expect: awards count: 6-10 (was 2), tier: None (T2)
```

---

## T2: Rank Tier System (10 tiers, Greek-themed)

**Files:** `build_index.py` (new function + apply), `index.html:133-144` (CSS), `index.html:340-352` (render)

**New function** in `build_index.py`:

```python
TIERS = [
    # (name, css_class, min_chunks, min_active_days)
    ("Apprentice",   "tier-apprentice",   1,    1),
    ("Disciple",     "tier-disciple",     10,   5),
    ("Philosopher",  "tier-philosopher",  50,   15),
    ("Architect",    "tier-architect",    100,  30),
    ("Artificer",    "tier-artificer",    250,  60),
    ("Luminary",     "tier-luminary",     500,  100),
    ("Titan",        "tier-titan",        1000, 150),
    ("Olympian",     "tier-olympian",     2500, 200),
    ("Prometheus",   "tier-prometheus",   5000, 300),
    ("Nous",         "tier-nous",         10000, 365),  # mythical tier
]

FOUNDER_NAME = "teknium"  # always max tier

def assign_tier(stats, name):
    """Return (tier_name, css_class, tier_index). Tiers are monotonic — only go up."""
    if name == FOUNDER_NAME:
        return ("Nous", "tier-nous", len(TIERS))  # max tier for founder
    chunks = stats.get("total_chunks", 0)
    days = stats.get("active_days", 0)
    # Find the highest tier the user qualifies for (BOTH thresholds)
    best = (TIERS[0][0], TIERS[0][1], 0)
    for i, (tname, tclass, min_chunks, min_days) in enumerate(TIERS):
        if chunks >= min_chunks and days >= min_days:
            best = (tname, tclass, i)
    return best
```

**Apply** in the leaderboard loop (after `medal = assign_medal(...)`):

```python
tier = assign_tier(s, author)
# ... then in the leaderboard.append():
"tier": {"name": tier[0], "class": tier[1], "index": tier[2]},
```

**CSS** in `index.html` (add after the existing `.badge-*` block at line 144):

```css
/* Rank tiers — Greek-themed, color-mapped to the Talio brand */
.tier-apprentice  { color: #7a8a9a; }
.tier-disciple    { color: #5eead4; }
.tier-philosopher { color: #14b8a6; }
.tier-architect   { color: #3b82f6; }
.tier-artificer   { color: #8b5cf6; }
.tier-luminary    { color: #f59e0b; }
.tier-titan       { color: #ef4444; }
.tier-olympian    { color: #ec4899; }
.tier-prometheus  { color: #facc15; text-shadow: 0 0 6px rgba(250,204,21,0.4); }
.tier-nous        {
    background: linear-gradient(90deg, #facc15, #ec4899, #8b5cf6);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
    font-weight: 800;
    letter-spacing: 0.5px;
}
.tier-icon { font-size: 0.85em; margin-right: 2px; }
```

**Update `renderBadges`** in `index.html:340-352` to also render the tier:

```javascript
function renderBadges(medal, awards, tier) {
  let html = '';
  if (tier && tier.name) {
    html += `<span class="badge-pill ${tier.class}">${tierIcon(tier.name)} ${esc(tier.name)}</span>`;
  }
  if (Array.isArray(medal) && medal.length === 2) {
    const [id, name] = medal;
    html += `<span class="badge-pill badge-${id}">${MEDAL_ICONS[id]||''} ${esc(name)}</span>`;
  }
  if (Array.isArray(awards)) {
    awards.forEach(a => {
      const [id, name, cat, rarity] = a;
      const cls = `badge-${id}` + (rarity && rarity !== 'bronze' ? ` rarity-${rarity}` : '');
      html += `<span class="badge-pill ${cls}" title="${esc(cat||'')} · ${esc(rarity||'')}">${esc(name)}</span>`;
    });
  }
  return html;
}
function tierIcon(name) {
  const m = {
    'Apprentice':'🌱','Disciple':'📜','Philosopher':'🦉','Architect':'🏛️',
    'Artificer':'🔨','Luminary':'💡','Titan':'⚡','Olympian':'🌟',
    'Prometheus':'🔥','Nous':'∞'
  };
  return m[name] || '◆';
}
```

**Rarity CSS** (add to index.html):

```css
.rarity-bronze  { box-shadow: inset 0 0 0 1px rgba(217,119,6,0.4); }
.rarity-silver  { box-shadow: inset 0 0 0 1px rgba(148,163,184,0.5); }
.rarity-gold    { box-shadow: inset 0 0 0 1px rgba(245,158,11,0.6), 0 0 6px rgba(245,158,11,0.15); }
.rarity-gilded  {
    background: rgba(245,158,11,0.18) !important;
    box-shadow: inset 0 0 0 1.5px rgba(245,158,11,0.7), 0 0 8px rgba(245,158,11,0.2);
    color: #facc15 !important;
}
```

**Verification:** Rebuild, check first author has tier. Expected: top author (probably teknium) = Nous, top 10 = Olympian or Prometheus.

```bash
python3 build_index.py 2>&1 | tail -3
python3 -c "import json; d=json.load(open('search-data.json')); [print(u['name'], '->', u['tier']['name'], '|', len(u['awards']), 'badges') for u in d['leaderboard'][:10]]"
```

---

## T3: Hidden Badges (4 of them, CSS reveal-on-hover)

**Files:** `build_index.py` (extend `assign_awards`), `index.html` (CSS + render)

Hidden badges: criteria that aren't about volume. Even a 1-message user can be a "Tech Priest" if they posted in `#developers`.

**Add to `assign_awards`** (separate hidden-eval block at the end):

```python
    # ---- HIDDEN (4 badges — evaluated in isolation, not gated on longevity) ----
    if s.get("first_channel") == "developers":
        awards.append(["tech_priest", "Tech Priest", "hidden", "gilded"])
    if s.get("night_owl_msgs", 0) >= 5:  # 5+ messages between 02:00–05:00 UTC
        awards.append(["night_owl", "Night Owl", "hidden", "gilded"])
    if s.get("rank_percentile", 100) <= 1:  # top 1% by chunks
        awards.append(["one_percent", "The 1%", "hidden", "gilded"])
    if s.get("hermes_herald", False):  # active in #hermes-agent on a known release date
        awards.append(["hermes_herald", "Herald of Hermes", "hidden", "gilded"])
    return awards
```

**Pre-compute the new signals** in the per-chunk loop:

```python
# In the per-chunk / per-author loop:
if ch == "developers" and s.get("first_channel") in (None, "", "developers"):
    s["first_channel"] = "developers"  # set only on first non-empty channel
# Night owl: count messages with hour in 2..5 UTC
for msg in c.get("messages", []):
    if msg.get("author") != author:
        continue
    try:
        h = datetime.fromisoformat(msg["timestamp"].replace("Z","+00:00")).hour
        if 2 <= h <= 5:
            s["night_owl_msgs"] = s.get("night_owl_msgs", 0) + 1
    except (KeyError, ValueError, TypeError):
        pass
# Hermes herald: active in #hermes-agent on a release date (known list, see below)
HERMES_RELEASE_DATES = {"2025-02-15", "2025-05-22", "2025-08-13", "2025-11-04",
                         "2026-02-12", "2026-05-21"}  # update per real release calendar
if ch == "hermes-agent" and chunk_date in HERMES_RELEASE_DATES:
    s["hermes_herald"] = True
```

**`rank_percentile`** is computed after the leaderboard is fully sorted (post-loop pass):

```python
# After the leaderboard is built, before serialization:
_sorted = sorted(leaderboard, key=lambda x: x["total_chunks"], reverse=True)
for i, u in enumerate(_sorted):
    u["rank_percentile"] = round((i + 1) / len(_sorted) * 100, 2)
```

**CSS** for hidden reveal-on-hover:

```css
.badge-hidden-locked {
    filter: grayscale(100%) brightness(0.55);
    cursor: help;
    position: relative;
}
.badge-hidden-locked::after {
    content: '?';
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    color: rgba(255,255,255,0.6);
    font-weight: 700;
}
.badge-hidden-locked:hover {
    filter: none;
}
.badge-hidden-locked:hover::after { content: ''; }
```

**Render** in `renderBadges`: hidden badges get `.badge-hidden-locked` class for users who haven't earned them (the lock silhouette), `.badge-tech_priest` etc. for users who have. Use a global `HIDDEN_BADGES = new Set(['tech_priest','night_owl','one_percent','hermes_herald'])` lookup. For v1, **always reveal hidden badges on the user's own profile** (they earned it, show it) and **lock them in the legend modal** (mystery, not full description).

**Verification:**
```bash
python3 build_index.py 2>&1 | tail -3
python3 -c "import json; d=json.load(open('search-data.json')); h=[u for u in d['leaderboard'] if any(a[2]=='hidden' for a in u['awards'])]; print('hidden-badge holders:', len(h), '| sample:', [(u['name'],[a[1] for a in u['awards'] if a[2]=='hidden']) for u in h[:3]])"
```

---

## T4: Gilded Tier (Multi-Domain Mastery)

**Files:** `build_index.py` (new function), `index.html` (CSS already in T2)

Gilded badges require mastery across **multiple categories** — they're not just "you hit a high number." This is the naysayer's anti-Foil pushback: a Gilded badge means "you showed up *broadly*, not just loudly."

**New function** in `build_index.py`:

```python
GILDED_REQUIREMENTS = {
    # "badge_id": [set_of_required_other_badges]
    "immortal":  {"pillar", "centurion"},      # 500 active days + 100-day tier
    "inferno":   {"streak", "kindled"},         # 180d streak + 90d streak
    "pioneer":   {"diplomat", "settler"},       # all-4-channels + 10 days + early
    "socialite": {"diplomat", "pillar"},        # same-day-all-channels + 4-channels + 50 days
    "elocutionist": {"sage", "centurion"},      # high-density + longevity
    "catalyst":  {"sage", "settler"},           # long thread + quality + presence
    # hidden badges are gilded by default (in T3)
}

def compute_gilded(awards):
    """Return list of badge IDs that qualify for Gilded (multi-domain mastery)."""
    earned = {a[0] for a in awards}
    gilded = []
    for badge_id, reqs in GILDED_REQUIREMENTS.items():
        if badge_id in earned and reqs.issubset(earned):
            gilded.append(badge_id)
    return gilded
```

**Apply** in the leaderboard loop:

```python
# After "awards": awards,
"gilded": compute_gilded(awards),
```

**Verification:**
```bash
python3 build_index.py 2>&1 | tail -3
python3 -c "import json; d=json.load(open('search-data.json')); g=[u for u in d['leaderboard'] if u['gilded']]; print('gilded holders:', len(g), '| sample:', [(u['name'], u['gilded']) for u in g[:3]])"
```

---

## T5: Progress Bars (Near-Miss Stats)

**Files:** `build_index.py` (compute `progress` block)

For every user, compute the next milestone they're approaching. The UI shows this as a progress bar on the profile.

```python
def compute_progress(s, tier_idx):
    """Return progress info for the user's three closest milestones."""
    progress = {"next_tier": None, "next_longevity": None, "next_streak": None}

    # Next tier
    if tier_idx + 1 < len(TIERS):
        nxt = TIERS[tier_idx + 1]
        progress["next_tier"] = {
            "name": nxt[0], "class": nxt[1],
            "current_chunks": s["total_chunks"], "target_chunks": nxt[2],
            "current_days": s["active_days"], "target_days": nxt[3],
        }

    # Next longevity badge (active_days)
    longevity_thresholds = [(10,"settler"),(50,"pillar"),(100,"centurion"),(250,"vanguard"),(500,"immortal")]
    days = s["active_days"]
    for t, b in longevity_thresholds:
        if days < t:
            progress["next_longevity"] = {"target": t, "current": days, "badge": b}
            break

    # Next streak badge
    streak_thresholds = [(7,"spark"),(30,"streak"),(90,"kindled"),(180,"inferno")]
    streak = s["max_streak"]
    for t, b in streak_thresholds:
        if streak < t:
            progress["next_streak"] = {"target": t, "current": streak, "badge": b}
            break

    return progress
```

**Apply** in the leaderboard loop:
```python
"progress": compute_progress(s, tier[2]),
```

**UI** in `index.html` profile card (T6 covers the full redesign; this snippet shows the progress bar):

```javascript
function renderProgress(progress) {
  if (!progress) return '';
  const items = [];
  if (progress.next_tier) {
    const nt = progress.next_tier;
    const pct = Math.max(0, Math.min(100, Math.round(
        100 * Math.min(nt.current_chunks/nt.target_chunks, nt.current_days/nt.target_days)
    )));
    items.push(`<div class="progress-row">
      <span class="progress-label">Next tier: ${esc(nt.name)}</span>
      <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
      <span class="progress-pct">${pct}%</span>
    </div>`);
  }
  if (progress.next_longevity) {
    const nl = progress.next_longevity;
    const pct = Math.round(100 * nl.current / nl.target);
    items.push(`<div class="progress-row">
      <span class="progress-label">Active days: ${nl.current}/${nl.target} → ${esc(nl.badge)}</span>
      <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
    </div>`);
  }
  if (progress.next_streak) {
    const ns = progress.next_streak;
    const pct = Math.round(100 * ns.current / ns.target);
    items.push(`<div class="progress-row">
      <span class="progress-label">Streak: ${ns.current}d/${ns.target}d → ${esc(ns.badge)}</span>
      <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
    </div>`);
  }
  return items.join('');
}
```

**Progress bar CSS:**
```css
.progress-row { display: flex; align-items: center; gap: 8px; margin: 4px 0; font-size: 0.78rem; }
.progress-label { flex: 0 0 auto; color: var(--text-muted); }
.progress-bar { flex: 1 1 auto; height: 6px; background: rgba(255,255,255,0.08); border-radius: 3px; overflow: hidden; }
.progress-fill { height: 100%; background: linear-gradient(90deg, #14b8a6, #facc15); transition: width 0.3s ease; }
.progress-pct { flex: 0 0 36px; text-align: right; color: var(--primary-light); font-variant-numeric: tabular-nums; }
```

---

## T6: Profile Page Redesign (Steam-Style)

**Files:** `index.html` (the existing user-lookup / profile modal)

**Current state:** The existing profile card is minimal. v5 redesign:

1. **Header band:** avatar (initials or generated SVG crest), handle, tier name + tier icon, funny title (existing), join date
2. **Stats strip:** Total chunks · Total messages · Active days · Max streak · Channels active
3. **Featured showcase:** 5-slot "best of" grid (highest-rarity badges they own, with Gilded first)
4. **Progress section:** Tier-up bar + 2 longest near-miss milestones (from T5)
5. **Full badge grid:** All earned badges, grouped by category (Longevity / Breadth / Quality / Milestone / Hidden), with rarity color. Hidden they own are shown; hidden they don't are locked silhouettes.
6. **Rivals / Neighbors:** 2 above, 2 below on the leaderboard (people with ±20% of their chunk count). Build this in the leaderboard post-pass:
   ```python
   # In compute_progress or a new compute_neighbors() — sort by chunks, find ±2 neighbors
   ```
7. **Recent activity:** Last 5 chunks they appeared in, with channel + date
8. **"Now in"** tag: most recent channel active in (last 7 days)

**Layout sketch (ASCII):**
```
┌────────────────────────────────────────────────────┐
│  ⓣ  teknium       [∞ Nous] [⚕ Founder]            │
│       "Architect of the Forge"                     │
│       Joined Dec 2024 · 1,247 days active          │
├────────────────────────────────────────────────────┤
│  18,432 msgs · 4,200 chunks · 412 days · 287d streak│
│  · 4 channels                                       │
├────────────────────────────────────────────────────┤
│  ┌──┐┌──┐┌──┐┌──┐┌──┐                              │
│  │VG││IM││SA││DI││CA│  Featured showcase (5 slots)  │
│  └──┘└──┘└──┘└──┘└──┘                              │
├────────────────────────────────────────────────────┤
│  Next tier: ⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯  82% (Nous → ???)         │
│  Active days: ████████████░░  412/500 → Immortal  │
│  Streak:        ██████████░░  287/365 → ???        │
├────────────────────────────────────────────────────┤
│  Longevity (4)   [Vanguard★] [Centurion★] ...      │
│  Breadth (3)     [Pioneer✦] [Diplomat★] [Explorer] │
│  Quality (2)     [Catalyst✦] [Sage★]              │
│  Hidden (2)      [Herald✦] [Night Owl✦]            │
│  Milestone (5)   [Artificer] [Luminary] ...         │
├────────────────────────────────────────────────────┤
│  Rivals: +2 above · +2 below on the leaderboard    │
│  alice  ↑ 4,580    bob  ↑ 4,310                    │
│  carol  ↓ 3,890    dave  ↓ 3,720                   │
├────────────────────────────────────────────────────┤
│  Recent: 2026-06-08 #hermes-agent · 2026-06-07 ... │
└────────────────────────────────────────────────────┘
```

**Verification:** Click any username in the leaderboard, confirm profile modal renders with all 8 sections. Check mobile (existing mobile layout in T11 of v4 — should compose cleanly).

---

## T7: Badge Legend Modal — Update for v5

**Files:** `index.html:313-328` (the existing `badgeModal`)

Replace the static list with a dynamic render of all known badges (the 5 medals + the ~28 awards), grouped by category, showing the rarity tier and a short description.

```javascript
const BADGE_CATALOG = [
  // medals
  {id:'caduceus', name:'Founder', cat:'Special', rarity:'gilded', desc:'teknium — creator of Nous Research', medal:true},
  {id:'gold',     name:'Gold',    cat:'Medal',  rarity:'gold',   desc:'Top 10 contributors', medal:true},
  {id:'silver',   name:'Silver',  cat:'Medal',  rarity:'silver', desc:'Top 11–25', medal:true},
  {id:'bronze',   name:'Bronze',  cat:'Medal',  rarity:'bronze', desc:'Top 26–50', medal:true},
  {id:'ribbon',   name:'Ribbon',  cat:'Medal',  rarity:'bronze', desc:'Everyone else — you showed up!', medal:true},
  // longevity
  {id:'settler',    name:'Settler',    cat:'Longevity', rarity:'bronze',  desc:'10+ active days'},
  {id:'pillar',     name:'Pillar',     cat:'Longevity', rarity:'silver',  desc:'50+ active days'},
  {id:'centurion',  name:'Centurion',  cat:'Longevity', rarity:'gold',    desc:'100+ active days'},
  {id:'vanguard',   name:'Vanguard',   cat:'Longevity', rarity:'gold',    desc:'250+ active days'},
  {id:'immortal',   name:'Immortal',   cat:'Longevity', rarity:'gilded',  desc:'500+ active days + multi-domain'},
  {id:'spark',      name:'Spark',      cat:'Longevity', rarity:'bronze',  desc:'7+ day activity streak'},
  {id:'streak',     name:'Streak',     cat:'Longevity', rarity:'silver',  desc:'30+ day activity streak'},
  {id:'kindled',    name:'Kindled',    cat:'Longevity', rarity:'gold',    desc:'90+ day activity streak'},
  {id:'inferno',    name:'Inferno',    cat:'Longevity', rarity:'gilded',  desc:'180+ day activity streak + multi-domain'},
  // breadth
  {id:'wanderer',   name:'Wanderer',   cat:'Breadth',   rarity:'bronze',  desc:'Active in 2+ channels'},
  {id:'explorer',   name:'Explorer',   cat:'Breadth',   rarity:'silver',  desc:'Active in 3+ channels'},
  {id:'diplomat',   name:'Diplomat',   cat:'Breadth',   rarity:'gold',    desc:'Active in all 4 channels'},
  {id:'pioneer',    name:'Pioneer',    cat:'Breadth',   rarity:'gilded',  desc:'Active in all 4 channels + present at launch + 10+ days'},
  {id:'socialite',  name:'Socialite',  cat:'Breadth',   rarity:'gilded',  desc:'Active in all 4 channels on the same day'},
  // quality
  {id:'sage',         name:'Sage',         cat:'Quality', rarity:'silver', desc:'High-value convos (15+ msgs/chunk avg)'},
  {id:'elocutionist', name:'Elocutionist', cat:'Quality', rarity:'gold',   desc:'30+ msgs/chunk avg + longevity'},
  {id:'catalyst',     name:'Catalyst',     cat:'Quality', rarity:'gilded', desc:'Started a long thread (30+ msgs) + quality + presence'},
  // milestone (tier-tied, not a separate row in the catalog — surfaced in profile header)
  // hidden (locked in modal; the lock silhouette speaks for itself)
];

function renderBadgeLegend() {
  const groups = {};
  BADGE_CATALOG.forEach(b => { (groups[b.cat] = groups[b.cat] || []).push(b); });
  return Object.entries(groups).map(([cat, items]) => `
    <div class="legend-group">
      <h4>${cat}</h4>
      ${items.map(b => `
        <div class="item">
          <span class="badge-pill badge-${b.id}${b.rarity!=='bronze'?' rarity-'+b.rarity:''}">${esc(b.name)}</span>
          <span style="color:var(--text-muted);font-size:.75rem">${esc(b.desc)}</span>
        </div>`).join('')}
    </div>`).join('');
}
```

Hidden badges are NOT in the catalog (the locked silhouette is the legend — discovery is half the fun). This is the naysayer's "scarcity over checklist" principle in action.

---

## T8: Run, Verify, Commit, Deploy

```bash
cd /mnt/homes/galileo/argo/Development/nous-community-expert-dev
python3 qa.py
python3 build_index.py
git add -A
git commit -m "v5: max-gamification — ranks, 28 badges, hidden, gilded, profile redesign, progress"
git push origin main keyargo github  # all 3 remotes per repo config
```

The 6h auto-deploy catches the new commit and deploys to Cloudflare Pages. Verify in browser:
- Click any leaderboard row → profile modal shows all 8 sections
- Open badge legend → see all 28+ badges grouped by category
- Open your own profile (the founder) → see Nous tier + Gilded mastery
- Open a 1-message contributor's profile → see Apprentice tier + Ribbon + at least one hidden candidate silhouette

---

## Verification Strategy

For each task, the verification is built into the task (a one-liner Python check or a UI smoke test). Cumulative:

```bash
# After T1-T5 (data layer):
python3 build_index.py && python3 -c "
import json
d = json.load(open('search-data.json'))
lb = d['leaderboard']
print(f'top 10: {[(u[\"name\"], u[\"tier\"][\"name\"], len(u[\"awards\"]), len(u[\"gilded\"])) for u in lb[:10]]}')
print(f'gilded holders: {sum(1 for u in lb if u[\"gilded\"])}')
print(f'progress block present: {all(\"progress\" in u and u[\"progress\"] for u in lb[:50])}')"
```

Expected: top author has tier=Nous, 6-10 awards, 1-3 gilded; 5-15 gilded holders across the 6177 users; progress blocks populated for all 100 leaderboard rows.

```bash
# After T6-T7 (UI):
# Manual: open index.html in browser, click 3 random users, verify profile shows all 8 sections
# Manual: open badge legend, verify all groups render
# Mobile: open in mobile viewport, verify profile composes without horizontal scroll
```

```bash
# Final:
python3 qa.py  # must pass
du -h search-data.json search-index.json  # must be < 30MB total
```

---

## Risks & Sequencing

| Risk | Mitigation |
|---|---|
| `awards[:2]` removal floods profiles with 10+ pills | Profile layout has a "+N more" collapse after 5 badges; full grid is in the profile modal |
| Tier thresholds too steep — most users stay Apprentice | Apprentices get the most *visually appealing* color (warm default); tier-up progress bar shows clear path |
| Hidden badge eval slow (per-message loop) | Only iterate `c.get("messages")` for hidden checks; cap to top-200 leaders if needed; pre-bucket by author |
| `HERMES_RELEASE_DATES` is hardcoded | Make it a config dict at top of `build_index.py`; comment that it should be updated per release calendar |
| Static asset budget creep | Each task's verification step checks file size; abort if search-data.json > 25MB |
| Profile modal too long on mobile | Existing mobile layout (T11 of v4) handles modal scroll; profile sections are collapsible if > 6 |

**Sequencing:** T0 → T1 → T2 → T3 → T4 → T5 (data layer, ~3 hours) → T6 → T7 (UI, ~2 hours) → T8 (ship, ~30 min). Total v1: ~5-6 hours.

---

## v2 Backlog (multi-day, deferred from v1)

- **"Since Last Watch" feed.** Commit `prev_stats.json` to the repo each build; diff current vs prev; output `recent_unlocks.json` with the top 20 unlocks. ~20 lines in `build_index.py`.
- **Tomes of Knowledge / Hidden Paths.** Algorithmic discovery feed: "Find the oldest message from teknium", "Read chunks tagged #computer-use", etc. No tracking — just curated search suggestions that change every build.
- **Year-in-Review SVG cards.** Pre-render for the top 500 users; render as static SVG; shareable on social. Surface "Your 2025 in Nous" only for users with ≥10 messages in 2025 (so it's relentlessly positive per the naysayer).
- **Achievement % unlock rates.** Pre-compute global unlock % per badge in the build; display "Only 2% of users have this" on the badge.
- **Gilded "Laurel" frame treatment.** Visual upgrade for gilded badges — an animated laurel wreath SVG that appears on hover. (CSS-only is v1; SVG is v2.)
- **Release date maintenance.** Make `HERMES_RELEASE_DATES` read from a `releases.json` checked into the repo, updated on each Hermes release PR.

## v3 Backlog (would need server, defer indefinitely on static architecture)

- Live "now playing" channel widget (needs WebSocket or polling endpoint)
- Real-time quest system with click tracking
- User-curated badge showcase (needs login)
- Per-user "send kudos" or endorsements (needs account)

---

## Changelog

- 2026-06-09 14:35 — **v5: Created. Plan for max-gamification expansion. Skeptic-validated design (power-law, no live feed, Gilded vs Foil). v1 = ~5-6 hours; v2/v3 backlog below.**
