# Talio Next Version — Implementation Plan

> **For Hermes:** Follow this plan task-by-task. The skeptic in the huddle was right on most things — search is the hero, points are secondary, virality comes from utility. Build in this order.

**Goal:** Evolve Talio from a functional Discord archive search into a polished, shareable, visually striking community intelligence tool.

**Architecture:** Pure static site (Cloudflare Pages). Build pipeline (`build_index.py`) generates JSON data files. Frontend (`index.html`) loads them client-side. No server, no API, no external JS dependencies. The build step is where "dynamic" logic lives (trending analysis, stats computation).

**Tech Stack:** Vanilla JS, Canvas API (graphs), Python stdlib (build scripts), Cloudflare Pages, GitHub Actions.

**Huddle Input:** The deepseekv4flash skeptic argued strongly against gamification of a static archive (hollow points, noise metrics, community backlash risk) and in favor of search precision, speed, mobile UX, and share-ability. The user still wants visual polish (winged sandal logo, embedded graphs, dynamic trending). This plan balances both by prioritizing search quality and visual design over retroactive point systems.

---
## Task 1: Winged Sandal Logo (Talaria)

**Objective:** Replace the generic "T" logo with a winged sandal icon representing Talio/Talaria (Hermes' winged sandals — messenger, speed).

**Files:**
- Modify: `logo.svg`

**Step 1: Design the SVG**

A simplified winged sandal: a horizontal sole/sandal shape with stylized wings sweeping upward from the heel. Minimal lines, matching the dark theme palette (cyan `#00a3d4` accent, some `#00d4aa`).

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 40" fill="none">
  <!-- Left wing -->
  <path d="M12 28 C4 22, 0 14, 2 8 C6 12, 10 18, 12 22 C14 16, 20 10, 28 6 C22 12, 18 18, 14 24 C18 22, 24 20, 30 20 C24 24, 18 26, 12 28Z" fill="#00a3d4" opacity="0.9"/>
  <!-- Right wing -->
  <path d="M38 28 C46 22, 50 14, 48 8 C44 12, 40 18, 38 22 C36 16, 30 10, 22 6 C28 12, 32 18, 36 24 C32 22, 26 20, 20 20 C26 24, 32 26, 38 28Z" fill="#00a3d4" opacity="0.9"/>
  <!-- Sole / sandal base -->
  <rect x="14" y="26" width="22" height="4" rx="2" fill="#00d4aa"/>
  <!-- Ankle strap -->
  <path d="M20 26 L18 18 L22 20 L20 26Z" fill="#00d4aa" opacity="0.7"/>
  <!-- Text -->
  <text x="52" y="26" font-family="Inter,-apple-system,sans-serif" font-size="20" font-weight="700" fill="#e0e8f0">Talio</text>
  <text x="52" y="34" font-family="Inter,-apple-system,sans-serif" font-size="9" fill="#7a8a9a" letter-spacing="2">COMMUNITY SEARCH</text>
</svg>
```

**Step 2: Update index.html header**

Change header section to:
```html
<div class="logo"><img src="logo.svg" alt="Talio" style="height:28px;vertical-align:middle"></div>
<h1>Talio</h1>
<p class="subtitle">Search the Nous Research community — <span id="statQuick">19K chunks, 292K messages</span></p>
```

**Step 3: Verify**

Open `index.html` locally with `serve.py`. Logo renders cleanly in the header.

**Step 4: Commit**

```bash
git add logo.svg index.html
git commit -m "feat: winged sandal logo (Talaria branding)"
```

---
## Task 2: Dynamic Trending via Build Step

**Objective:** Replace hardcoded `SAMPLE_QUERIES` in `build_index.py` with dynamically extracted trending terms from the most recent archive chunks. No client-side computation — done during build.

**Files:**
- Modify: `build_index.py`

**Step 1: Add trending extraction function**

Add to `build_index.py` before `main()`:

```python
def extract_trending(chunks, count=12):
    """Extract trending terms from recent chunks using simple TF."""
    from collections import Counter
    import re

    # Get chunks from last 7 days
    if not chunks:
        return SAMPLE_QUERIES  # fallback to defaults

    now = datetime.utcnow()
    recent = []
    for c in chunks:
        et = c.get("end_time", "") or c.get("start_time", "")
        if et:
            try:
                d = datetime.fromisoformat(et.replace("Z", "+00:00"))
                if (now - d).days <= 7:
                    recent.append(c.get("text", ""))
            except (ValueError, TypeError):
                recent.append(c.get("text", ""))

    if len(recent) < 50:
        # Fallback: just use all chunks sorted by most recent
        sorted_chunks = sorted(chunks, key=lambda c: c.get("end_time", "") or c.get("start_time", "") or "", reverse=True)
        recent = [c.get("text", "") for c in sorted_chunks[:200]]

    # Extract meaningful bigrams and trigrams
    stopwords = {"the","and","for","with","that","this","from","have","will","just",
        "about","what","when","they","your","you","are","can","not","but","all","any",
        "our","has","had","was","were","been","into","out","like","make","get","use",
        "work","know","think","take","come","way","good","more","some","time","very",
        "now","than","then","only","also","could","should","would","thats","dont",
        "im","ive","hes","shes","lets","https","http","www","com","bot","user",
        "msg","id","num","val","etc","api","url","json"}

    word_counts = Counter()
    for text in recent:
        words = [w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 3 and w not in stopwords and not w.isdigit()]
        for w in words:
            word_counts[w] += 1

    # Get top terms, build phrases from pair frequency
    top_words = [w for w, _ in word_counts.most_common(30)]

    # Build 2-word phrases from top words
    phrases = []
    for text in recent:
        tl = text.lower()
        for i in range(len(top_words) - 1):
            phrase = f"{top_words[i]} {top_words[i+1]}"
            if phrase in tl and phrase not in phrases:
                phrases.append(phrase)
                if len(phrases) >= count:
                    break
        if len(phrases) >= count:
            break

    return phrases[:count] if len(phrases) >= count else SAMPLE_QUERIES[:count]
```

**Step 2: Update main() to use extract_trending**

Replace:
```python
"sample_queries": SAMPLE_QUERIES,
```
With:
```python
"sample_queries": extract_trending(chunks),
```

Also add `from datetime import datetime` at top (already imported).

**Step 3: Test locally**

```bash
cd /mnt/homes/galileo/argo/Development/nous-community-expert-dev
python3 build_index.py /mnt/homes/galileo/argo/Development/nous-discord-archive/tools/chunks.jsonl 2>&1 | tail -5
```

Then check:
```bash
python3 -c "import json; d=json.load(open('search-data.json')); print(d.get('sample_queries',[]))"
```
Expected: 12 dynamically extracted terms (not the hardcoded list).

**Step 4: Commit**

```bash
git add build_index.py
git commit -m "feat: dynamic trending from recent archive chunks"
```

---
## Task 3: Embed Graphs Inline at Bottom

**Objective:** Move `graphs.html` Canvas charts into the main `index.html` at the bottom. Remove the separate graphs page link. Keep the design clean — the graphs appear after all search results as a "Community Activity" section.

**Files:**
- Modify: `index.html`
- Keep: `graphs.html` (for reference, can delete later)

**Step 1: Add graph section to index.html**

Add right before the `<footer>` element:

```html
<div id="graphSection" style="margin:2rem 0;display:none">
  <div class="section-label">📊 Community Activity</div>
  <div class="chart-grid">
    <div class="chart-box">
      <div style="display:flex;justify-content:space-between;margin-bottom:.4rem">
        <span style="color:var(--text-muted);font-size:.8rem">Messages per day</span>
        <span id="graphRange" style="display:flex;gap:.3rem">
          <span class="tag active" onclick="setGraphRange(30)">30d</span>
          <span class="tag" onclick="setGraphRange(60)">60d</span>
          <span class="tag" onclick="setGraphRange(90)">90d</span>
          <span class="tag" onclick="setGraphRange(0)">All</span>
        </span>
      </div>
      <canvas id="graphDaily" style="width:100%;height:180px;background:var(--bg);border-radius:6px"></canvas>
    </div>
    <div class="chart-box">
      <div style="color:var(--text-muted);font-size:.8rem;margin-bottom:.4rem">Channel distribution</div>
      <canvas id="graphChannel" style="width:100%;height:120px;background:var(--bg);border-radius:6px"></canvas>
    </div>
  </div>
  <div class="chart-grid">
    <div class="chart-box">
      <div style="color:var(--text-muted);font-size:.8rem;margin-bottom:.4rem">Activity by hour (UTC)</div>
      <canvas id="graphHour" style="width:100%;height:120px;background:var(--bg);border-radius:6px"></canvas>
    </div>
    <div class="chart-box">
      <div style="color:var(--text-muted);font-size:.8rem;margin-bottom:.4rem">Top contributors</div>
      <canvas id="graphAuthors" style="width:100%;height:120px;background:var(--bg);border-radius:6px"></canvas>
    </div>
  </div>
</div>
```

**Step 2: Add CSS for chart-grid**

Add to `<style>`:
```css
.chart-grid{display:grid;grid-template-columns:1fr 1fr;gap:.8rem;margin-bottom:.8rem}
.chart-box{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:.8rem}
@media(max-width:640px){.chart-grid{grid-template-columns:1fr}}
```

**Step 3: Port Canvas rendering functions from graphs.html**

Copy the `renderDaily()`, `renderChannel()`, `renderHour()`, `renderAuthor()` JS functions from `graphs.html` into `index.html`, adapted to use the new canvas IDs (`graphDaily`, `graphChannel`, `graphHour`, `graphAuthors`).

Key changes from graphs.html version:
- Use `window._graphRange = 30` as default
- `setGraphRange(n)` updates range and re-renders
- Remove the stats-row (already in header)
- Call `initGraphs()` after data loads, inside the `loadData()` success handler

**Step 4: Show graphs section after data loads**

In `loadData()`, after all renders:
```js
document.getElementById('graphSection').style.display = 'block';
initGraphs();
```

**Step 5: Remove separate graphs link from footer**

Replace the `<a href="graphs.html">📊 Graphs</a>` link with nothing — graphs are now inline.

**Step 6: Test locally**

Open locally with `serve.py`. Verify:
- Graphs section appears below results with 4 charts
- 30d/60d/90d/All toggle works
- Charts render without JS errors
- Responsive: on narrow screens, charts stack vertically

**Step 7: Commit**

```bash
git add index.html
git commit -m "feat: embed activity graphs inline at bottom of page"
```

---
## Task 4: Search Keyword Highlighting in Snippets

**Objective:** Highlight the user's search terms within result text snippets. Makes it immediately obvious WHERE the match occurred.

**Files:**
- Modify: `index.html`

**Step 1: Add highlight function**

Add to JS:
```js
function highlightText(text, query) {
  if (!query || !text) return escHtml(text);
  const escaped = escHtml(text);
  const terms = query.toLowerCase().split(/[^a-z0-9]+/).filter(t => t.length > 1);
  if (!terms.length) return escaped;
  let result = escaped;
  for (const term of terms) {
    const regex = new RegExp(`(${term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
    result = result.replace(regex, '<mark style="background:rgba(0,163,212,0.3);color:var(--primary-light);border-radius:2px;padding:0 2px">$1</mark>');
  }
  return result;
}
```

**Step 2: Update renderChunk to use highlightText**

In `renderChunk()`, change the text rendering from:
```js
const etxt = escHtml(txt);
```
To:
```js
const highlighted = highlightText(txt, document.getElementById('si').value);
```

And render with:
```html
<div class=txt id=t_${id}>${highlighted.length > 250 ? highlighted.slice(0,250)+'…' : highlighted}</div>
```

**Step 3: Test**

Search for a specific term like "plugin" or "training". Verify that matching terms in result snippets are highlighted in cyan.

**Step 4: Commit**

```bash
git add index.html
git commit -m "feat: keyword highlighting in search result snippets"
```

---
## Task 5: Shareable Search & Profile URLs

**Objective:** Allow sharing both search results AND user profiles via URL. `#q=search+terms` auto-searches. `#p=4rgo` auto-opens that user's profile card. Shareable profile cards with a "Share this profile" button.

**Files:**
- Modify: `index.html`

**Step 1: Add URL hash reading on load**

In the `loadData()` function (or in a new `initFromURL()` called at end of `loadData`):
```js
function initFromURL() {
  const hash = window.location.hash.slice(1);
  if (!hash) return;
  const params = new URLSearchParams(hash);
  if (params.has('q')) {
    document.getElementById('si').value = params.get('q');
    search();
  }
  if (params.has('author')) {
    document.getElementById('af').value = params.get('author');
    document.getElementById('adv').classList.add('open');
    if (!params.has('q')) search();
  }
}
```

**Step 2: Update search() to write URL hash**

At the end of the `search()` function, add:
```js
const q = document.getElementById('si').value.trim();
const author = document.getElementById('af').value.trim();
const hashParams = new URLSearchParams();
if (q) hashParams.set('q', q);
if (author) hashParams.set('author', author);
window.location.hash = hashParams.toString() ? '#' + hashParams.toString() : '';
```

**Step 3: Add "Share this search" button**

In the results header area (after "Showing X–Y of Z results"), add:
```html
${navigator.share ? `<span class="tag" onclick="shareSearch()" style="float:right;padding:.2rem .6rem">📤 Share</span>` : ''}
```

And the share function:
```js
function shareSearch() {
  const url = window.location.href;
  if (navigator.share) {
    navigator.share({ title: 'Talio Search', url });
  } else {
    navigator.clipboard.writeText(url).then(() => {
      // Show brief "Copied!" feedback
    });
  }
}
```

**Step 4: Test**

- Search for "GRPO training"
- Verify URL changes to `#q=GRPO+training`
- Copy URL, open in new tab
- Verify search auto-runs with same results
- Test author filter URL: `#q=&author=4rgo`

**Step 5: Add profile URL sharing (#p=username)**

In `initFromURL()`:
```js
if (params.has('p')) {
  lookupUser(params.get('p'));
  // Don't auto-search
}
```

In `lookupUser()`, after showing profile card, update URL hash:
```js
const hashParams = new URLSearchParams(window.location.hash.slice(1));
hashParams.set('p', username);
window.location.hash = '#' + hashParams.toString();
```

**Step 6: Add "Share this profile" button in profile card**

Add to the profile card HTML in `showProfile()`:
```html
<div style="margin-top:.6rem">
  <span class="tag" onclick="shareProfile('${esc(a.name)}')" style="font-size:.7rem;padding:.2rem .6rem">📤 Share profile</span>
  <span class="tag" onclick="shareProfileCard('${esc(a.name)}')" style="font-size:.7rem;padding:.2rem .6rem">🖼️ Share as image</span>
</div>
```

**Step 7: Test profile sharing**

- Lookup user "4rgo" → verify URL changes to `#p=4rgo`
- Copy URL, open new tab → verify profile card auto-opens
- Verify search still works alongside profile URL

**Step 8: Commit**

```bash
git add index.html
git commit -m "feat: shareable profile URLs (#p=username)"
```

---
## Task 6: Badge Redesign — Medals + Contribution Awards

**Objective:** Replace all badges with a medal system (Gold/Silver/Bronze/Ribbon) based on rank plus stable contribution awards that only go up (never reset). Everything computed automatically in the build step.

**Files:**
- Modify: `index.html` (badge rendering, CSS shapes)
- Modify: `build_index.py` (badge assignment logic)

**Step 1: Define the badge system**

**Medal Tiers** (everyone gets exactly one, based on current rank):

| Medal | Color | Icon | Criteria |
|-------|-------|------|----------|
| Gold 🏆 | Gold `#f59e0b` | Trophy | Top 10 |
| Silver 🥈 | Silver `#94a3b8` | Medal | Top 25 |
| Bronze 🥉 | Bronze `#d97706` | Medal | Top 50 |
| Ribbon 🎀 | Muted `#7a8a9a` | Ribbon | Everyone else |

These are rank-based but rank is computed from cumulative messages + chunks, which changes slowly for established users. A top-10 contributor stays top-10 unless someone surges past them.

**Contribution Awards** (optional, max 1-2 per user, based on lifetime stats that only go UP):

| Award | Color | Icon | Criteria | Why it's stable |
|-------|-------|------|----------|-----------------|
| Explorer 🌐 | Cyan `#00a3d4` | Compass | Active in 3+ channels | Channel count only increases |
| Streak 🔥 | Orange `#ef9234` | Flame | 30+ day max streak | Streak can only grow or stay |
| Pioneer 🏛️ | Purple `#8b5cf6` | Column | Active since archive start | Date never changes |
| Pillar 📊 | Teal `#14b8a6` | Bar chart | 50+ active days | Day count only increases |
| Sage 🧠 | Emerald `#10b981` | Brain | >15 msgs per chunk avg | Avg drifts slowly |
| Diplomat 🤝 | Blue `#3b82f6` | Handshake | Active in all 4 channels | All-or-nothing, once earned stays |

**Key stability insight from the huddle:** These badges are based on **accumulated lifetime stats** — once you earn them, you can't lose them. Channels count only goes up. Days active only goes up. Average msg/chunk drifts slowly. This avoids the "today you're a Night Owl, tomorrow you're not" problem.

**What changes from current:**
- No more 🏆 Legend, 🌐 Explorer, 📝 Prolific, 🏛️ Founding emoji badges
- Clean medal system (Gold/Silver/Bronze/Ribbon) everyone understands
- Contribution awards that are earned and kept
- Everything generated automatically in build_index.py
- Nothing that changes day-to-day in a volatile way

**Step 2: Create Caduceus SVG icon**

Create `caduceus.svg` — minimal winged staff with intertwined snakes:
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" width="14" height="14">
  <!-- Staff -->
  <line x1="12" y1="2" x2="12" y2="22" stroke="#f59e0b" stroke-width="1.5" stroke-linecap="round"/>
  <!-- Wings at top -->
  <path d="M12 4 C8 4, 4 6, 2 8 C4 6, 8 5, 12 5Z" fill="#f59e0b" opacity="0.8"/>
  <path d="M12 4 C16 4, 20 6, 22 8 C20 6, 16 5, 12 5Z" fill="#f59e0b" opacity="0.8"/>
  <!-- Snake 1 (left curve) -->
  <path d="M12 8 C8 10, 6 14, 8 16 C10 18, 12 16, 12 14" stroke="#f59e0b" stroke-width="0.8" fill="none"/>
  <!-- Snake 2 (right curve) -->
  <path d="M12 8 C16 10, 18 14, 16 16 C14 18, 12 16, 12 14" stroke="#f59e0b" stroke-width="0.8" fill="none"/>
  <!-- Eyes -->
  <circle cx="9.5" cy="13" r="0.5" fill="#f59e0b"/>
  <circle cx="14.5" cy="13" r="0.5" fill="#f59e0b"/>
</svg>
```

**Step 3: Update build_index.py badge logic**

Simplify badge assignment to medal + awards:
```python
def assign_medal(rank):
    """Assign a medal based on rank. Medals shift slowly with rank changes."""
    if rank is not None:
        if rank <= 10: return ("gold", "Gold 🏆")
        if rank <= 25: return ("silver", "Silver 🥈")
        if rank <= 50: return ("bronze", "Bronze 🥉")
    return ("ribbon", "Ribbon 🎀")

def assign_awards(name, total_chunks, total_messages, channels_active, dates_active, max_streak, msg_per_chunk):
    """Assign contribution awards based on lifetime stats (only go up, never reset)."""
    awards = []
    
    # Explorer: active in 3+ channels (channel count only increases)
    if channels_active and channels_active >= 3:
        awards.append(("explorer", "Explorer 🌐"))
    
    # Pioneer: active since archive start (date never changes)
    if first_seen and first_seen <= "2025-01-01":
        awards.append(("pioneer", "Pioneer 🏛️"))
    
    # Streak: 30+ day max streak (streak only grows or stays)
    if max_streak and max_streak >= 30:
        awards.append(("streak", "Streak 🔥"))
    
    # Pillar: 50+ active days (day count only increases)
    if len(dates_active) >= 50:
        awards.append(("pillar", "Pillar 📊"))
    
    # Sage: high depth per chunk (avg drifts slowly)
    if msg_per_chunk and msg_per_chunk > 15:
        awards.append(("sage", "Sage 🧠"))
    
    # Diplomat: active in all 4 channels (all-or-nothing, once earned stays)
    if channels_active and channels_active >= 4:
        awards.append(("diplomat", "Diplomat 🤝"))
    
    return awards  # May be empty for new/low-activity users
```

Store badges in leaderboard:
```python
{
    "medal": {"id": "gold", "name": "Gold 🏆"},
    "awards": [{"id": "explorer", "name": "Explorer 🌐"}],
}
```

**Step 4: Update index.html badge rendering**

New CSS for badge pills:
```css
.badge-pill{font-size:.65rem;padding:.15rem .55rem;border-radius:10px;display:inline-flex;align-items:center;gap:.25rem;line-height:1;font-weight:600;letter-spacing:.3px}
/* Medals */
.badge-gold{background:rgba(245,158,11,0.15);color:#f59e0b;border:1px solid rgba(245,158,11,0.3)}
.badge-silver{background:rgba(148,163,184,0.15);color:#94a3b8;border:1px solid rgba(148,163,184,0.3)}
.badge-bronze{background:rgba(217,119,6,0.15);color:#d97706;border:1px solid rgba(217,119,6,0.3)}
.badge-ribbon{background:rgba(122,138,154,0.1);color:#7a8a9a;border:1px solid rgba(122,138,154,0.15)}
/* Awards */
.badge-explorer{background:rgba(0,163,212,0.12);color:#00a3d4;border:1px solid rgba(0,163,212,0.25)}
.badge-pioneer{background:rgba(139,92,246,0.12);color:#8b5cf6;border:1px solid rgba(139,92,246,0.25)}
.badge-streak{background:rgba(239,146,52,0.12);color:#ef9234;border:1px solid rgba(239,146,52,0.25)}
.badge-pillar{background:rgba(20,184,166,0.12);color:#14b8a6;border:1px solid rgba(20,184,166,0.25)}
.badge-sage{background:rgba(16,185,129,0.12);color:#10b981;border:1px solid rgba(16,185,129,0.25)}
.badge-diplomat{background:rgba(59,130,246,0.12);color:#3b82f6;border:1px solid rgba(59,130,246,0.25)}
```

Render medals + awards in leaderboard:
```js
function renderBadges(medal, awards) {
  let html = '';
  if (medal) {
    const icons = {gold:'🏆', silver:'🥈', bronze:'🥉', ribbon:'🎀'};
    html += `<span class="badge-pill badge-${medal.id}">${icons[medal.id]||''} ${medal.name.split(' ')[0]}</span>`;
  }
  if (awards && awards.length) {
    awards.slice(0,2).forEach(a => {
      html += `<span class="badge-pill badge-${a.id}">${a.name.split(' ')[1]||''} ${a.name.split(' ')[0]}</span>`;
    });
  }
  return html;
}
```

**Step 5: Update badge legend modal**

The badge legend shows medals then awards:
```html
<h3>🏅 Medals</h3>
<div class="item"><span class="badge-pill badge-gold">🏆 Gold</span><span style="color:var(--text-muted);font-size:.75rem">Top 10 contributors</span></div>
<div class="item"><span class="badge-pill badge-silver">🥈 Silver</span><span style="color:var(--text-muted);font-size:.75rem">Top 25 contributors</span></div>
<div class="item"><span class="badge-pill badge-bronze">🥉 Bronze</span><span style="color:var(--text-muted);font-size:.75rem">Top 50 contributors</span></div>
<div class="item"><span class="badge-pill badge-ribbon">🎀 Ribbon</span><span style="color:var(--text-muted);font-size:.75rem">Everyone else — you showed up!</span></div>
<h3 style="margin-top:1rem">🎖️ Contribution Awards</h3>
<div class="item"><span class="badge-pill badge-explorer">🌐 Explorer</span><span style="color:var(--text-muted);font-size:.75rem">Active in 3+ channels</span></div>
<div class="item"><span class="badge-pill badge-pioneer">🏛️ Pioneer</span><span style="color:var(--text-muted);font-size:.75rem">Active since archive began</span></div>
<div class="item"><span class="badge-pill badge-streak">🔥 Streak</span><span style="color:var(--text-muted);font-size:.75rem">30+ day activity streak</span></div>
<div class="item"><span class="badge-pill badge-pillar">📊 Pillar</span><span style="color:var(--text-muted);font-size:.75rem">50+ active days</span></div>
<div class="item"><span class="badge-pill badge-sage">🧠 Sage</span><span style="color:var(--text-muted);font-size:.75rem">High-value conversations (15+ msgs/chunk)</span></div>
<div class="item"><span class="badge-pill badge-diplomat">🤝 Diplomat</span><span style="color:var(--text-muted);font-size:.75rem">Active in all 4 channels</span></div>
<p style="color:var(--text-muted);font-size:.75rem;margin-top:.8rem">Awards are earned from lifetime stats and never revoked.</p>
```

**Step 6: Test**

Build:
```bash
python3 build_index.py /mnt/homes/galileo/argo/Development/nous-discord-archive/tools/chunks.jsonl 2>&1 | tail -3
```

Check teknium has Caduceus:
```bash
python3 -c "import json; lb=json.load(open('search-data.json'))['leaderboard']; print(lb[0].get('badges',[]))"
```
Expected: teknium's first badge is the Caduceus (founder mark).

Check tiers on page load:
- S-Tier users have diamond icon + cyan pill
- D-Tier users have dot icon + muted pill
- No emoji text anywhere in badges

**Step 7: Commit**

```bash
git add build_index.py index.html caduceus.svg
git commit -m "feat: visual tier badge system with Founder Caduceus"
```
 
---
## Task 7: Mobile Responsive Layout

Current CSS has only one `@media(max-width:640px)` block. Expand it:

```css
@media(max-width:768px){
  .board{flex-direction:column}
  .board>div{min-width:100%}
  .srch{flex-direction:column}
  .srch button{width:100%}
  .flt{flex-wrap:wrap;gap:.3rem}
  .lookup{flex-direction:column;align-items:stretch;gap:.3rem}
  .lookup input{width:100%}
  .stats{gap:.8rem}
  .stat .num{font-size:1.1rem}
  .stat .label{font-size:.6rem}
  h1{font-size:1.3rem}
  .sidebar{flex-direction:column}
  .result .meta{flex-wrap:wrap;gap:.3rem;font-size:.65rem}
  .chart-grid{grid-template-columns:1fr}
  .container{padding:1rem .6rem}
}
```

**Step 2: Add collapsible sidebar**

Leaderboard, streaks, top days should be collapsed on mobile with a toggle:

```html
<div class="sidebar-toggle" onclick="toggleSidebar()">
  <span id="sidebarToggle">📊 Show leaderboard & stats</span>
</div>
<div id="sidebarContent" style="display:none">
  ... existing board content ...
</div>
```

```js
function toggleSidebar() {
  const el = document.getElementById('sidebarContent');
  const btn = document.getElementById('sidebarToggle');
  const isOpen = el.style.display !== 'none';
  el.style.display = isOpen ? 'none' : 'block';
  btn.textContent = isOpen ? '📊 Show leaderboard & stats' : '✕ Hide leaderboard';
}
```

On desktop (`min-width: 769px`), sidebar is always visible. Use CSS:
```css
@media(min-width:769px){.sidebar-toggle{display:none}#sidebarContent{display:block!important}}
@media(max-width:768px){#sidebarContent{display:none}}
```

**Step 3: Test on narrow viewport**

Use browser DevTools to emulate mobile (375px width). Verify:
- Stats row wraps to 2x2 grid
- Search box is full-width
- Leaderboard/stats collapsed behind toggle
- Results are full-width
- Graphs stack vertically
- All text is readable without zooming

**Step 4: Commit**

```bash
git add index.html
git commit -m "feat: mobile responsive layout with collapsible sidebar"
```

---
## Task 8: Service Worker for Instant Cache

**Objective:** Cache `search-data.json` and `search-index.json` in a service worker so returning visitors get instant load. No network wait on repeat visits.

**Files:**
- Create: `sw.js`
- Modify: `index.html`

**Step 1: Create service worker**

`sw.js`:
```js
const CACHE = 'talio-v1';
const ASSETS = [
  '/',
  '/index.html',
  '/search-data.json',
  '/search-index.json',
  '/metadata.json',
  '/logo.svg',
  '/favicon.svg',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll(ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k !== CACHE).map(k => caches.delete(k))
    ))
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  e.respondWith(
    caches.match(e.request).then(cached => {
      const fetchPromise = fetch(e.request).then(response => {
        if (response.ok && e.request.method === 'GET') {
          const clone = response.clone();
          caches.open(CACHE).then(cache => cache.put(e.request, clone));
        }
        return response;
      }).catch(() => cached);
      return cached || fetchPromise;
    })
  );
});
```

**Step 2: Register service worker in index.html**

Add before closing `</script>` tag:
```js
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js');
  });
}
```

**Step 3: Verify**

Open site in browser, check Application → Service Workers tab. Service worker should be registered and active. Reload — subsequent loads should load instantly from cache.

**Step 4: Commit**

```bash
git add sw.js index.html
git commit -m "feat: service worker for instant cached reload"
```

---
## Task 9: Clean Up Graphs Page & Footer

**Objective:** Remove the now-redundant `graphs.html` link from footer. Keep `graphs.html` file as a reference for now but remove the nav link.

**Files:**
- Modify: `index.html` (footer)

**Step 1: Update footer**

Current footer links:
```html
<a href="graphs.html">📊 Graphs</a> ·
<a href="https://github.com/KeyArgo/nous-community-expert-dev">⭐ Star on GitHub</a> ·
<a href="LICENSE">MIT License</a>
```

Remove the Graphs link since it's now inline.

**Step 2: Commit**

```bash
git add index.html
git commit -m "chore: remove graphs page link (now embedded)"
```

---
## Verification Checklist

Run after ALL tasks complete:

- [ ] `python3 build_index.py` completes without errors
- [ ] `search-data.json` < 25MB after build
- [ ] `search-index.json` < 25MB after build
- [ ] `python3 serve.py 8081` serves site without errors
- [ ] Browser loads page: stats, trending, leaderboard all render
- [ ] Trending shows dynamic terms (not the old hardcoded list)
- [ ] Search highlights matching keywords in results
- [ ] Share URL: `http://localhost:8081/#q=test` auto-searches for "test"
- [ ] Profile URL: `http://localhost:8081/#p=4rgo` auto-opens profile card
- [ ] Share profile button copies profile URL
- [ ] Embed graphs render at bottom, toggles work
- [ ] Mobile viewport (375px): sidebar collapsed, search full-width
- [ ] Service worker registered and caching
- [ ] Badges: medal system (Gold/Silver/Bronze/Ribbon) + awards (Explorer, Pioneer, Streak, Pillar, Sage, Diplomat)
- [ ] Gold medal shows for top 10, Silver for top 25, Bronze for top 50, Ribbon for everyone else
- [ ] Logo (winged sandals) renders in header
- [ ] `git push` → GitHub Actions → Cloudflare Pages deploy succeeds

---
## Cloudflare Pages Build Command (Update)

The build command in Cloudflare Pages settings stays the same — it already generates the data files:
```
git clone --depth 1 https://github.com/teknium1/nous-discord-archive.git /tmp/archive && python3 parse_archive.py /tmp/archive/archives /tmp/chunks.jsonl && python3 build_index.py /tmp/chunks.jsonl
```

No change needed here — `build_index.py` now includes dynamic trending, so the Cloudflare build will automatically produce fresh trending terms on every deploy.

---
## Risks & Open Questions

1. **Service worker cache invalidation on CF:** Cloudflare Pages doesn't give fine-grained cache headers for service workers. The `CACHE` version string (`talio-v1`) handles this — bump it when data format changes.

2. **Dynamic trending quality:** The TF-based extraction from recent chunks may produce noisy or single-word results. If quality is poor, fall back to a hybrid: top 6 dynamic + 6 curated defaults.

3. **Mobile sidebar toggle UX:** Users on mobile who want to see leaderboard need to tap once. Acceptable trade-off — desktop users get it inline.

4. **Points/gamification NOT included:** Per the skeptic's recommendation and the user's earlier feedback about points being unclear, this plan does NOT add a points system. The leaderboard + streak + badges + profile cards provide sufficient community recognition without the hollow gamification trap.

5. **Future consideration — "Share this search as embed":** After the base tasks, consider adding an OG image generated at build time for social previews when sharing search URLs. Requires server-side rendering of search previews → not feasible in current static architecture without a backend.
