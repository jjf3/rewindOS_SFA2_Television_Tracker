"""
RewindOS Reddit Tracker (Starfleet-Academy-style refactor)
---------------------------------------------------------
This is a reusable “show tracker” script (same pattern as the Starfleet Academy tracker):
- One SHOW config block (name/slug/subreddits/query terms)
- Consistent output naming: out/<slug>_*.csv + out/dashboard_<slug>.html
- Episode thread detection (S01E01 / 1x01 / "Episode 3" / "Ep. 3")
- Trailer detection
- Comment history snapshots for time-series plots (re-run on a schedule)
- Polite, no-auth Reddit JSON search (best-effort, rate-limit aware)

Usage (PowerShell):
  $env:SHOW_SLUG="Starfleet_Academy"
  $env:SHOW_NAME="Starfleet_Academy"
  $env:SUBREDDITS="television,startrek"   # comma-separated
  $env:QUERY_TERMS='"Starfleet Academy",Academy, SFA'  # comma-separated (quotes allowed)
  python .\show_reddit_tracker.py

Optional env:
  LIMIT=100
  OTHER_N=5
  SORT=new            # new | top | relevance
  T=all               # all | year | month | week | day
  USER_AGENT="RewindOS-SubTracker/1.0 (personal project; respectful polling)"
"""

import csv
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple

import requests


# -----------------------------
# Show Config (Starfleet-Academy style)
# -----------------------------
SHOW_SLUG = os.environ.get("SHOW_SLUG", "starfleet_academy").strip()
SHOW_NAME = os.environ.get("SHOW_NAME", "Starfleet Academy").strip()

# Comma-separated lists
SUBREDDITS_RAW = os.environ.get("SUBREDDITS", "television").strip()
SUBREDDITS = [s.strip() for s in SUBREDDITS_RAW.split(",") if s.strip()]

QUERY_TERMS_RAW = os.environ.get("QUERY_TERMS", SHOW_NAME).strip()
# allow CSV-ish values like: '"Star Trek: Starfleet Academy",Starfleet Academy'
QUERY_TERMS = [q.strip() for q in QUERY_TERMS_RAW.split(",") if q.strip()]

LIMIT = int(os.environ.get("LIMIT", "100"))
OTHER_POSTS_N = int(os.environ.get("OTHER_N", "5"))
SORT = os.environ.get("SORT", "new")
TIME_FILTER = os.environ.get("T", "all")

USER_AGENT = os.environ.get(
    "USER_AGENT",
    "RewindOS-SubTracker/1.0 (personal project; respectful polling)"
)

# If you want to be stricter about what counts as “official trailer”
TRAILER_KEYWORDS = [
    "official trailer",
    "teaser trailer",
    "trailer",
    "teaser",
]


# -----------------------------
# Paths (consistent RewindOS layout)
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUT_DIR = os.path.join(BASE_DIR, "out")
LOG_DIR = os.path.join(BASE_DIR, "logs")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, f"{SHOW_SLUG}_tracker.log")

ALL_POSTS_CSV = os.path.join(OUT_DIR, f"{SHOW_SLUG}_all_posts.csv")
EPISODE_POSTS_CSV = os.path.join(OUT_DIR, f"{SHOW_SLUG}_episode_posts.csv")
SELECTED_POSTS_CSV = os.path.join(OUT_DIR, f"{SHOW_SLUG}_selected_posts.csv")
COMMENT_HISTORY_CSV = os.path.join(DATA_DIR, f"{SHOW_SLUG}_comment_history.csv")

EPISODE_PLOT_PNG = os.path.join(OUT_DIR, f"{SHOW_SLUG}_episode_comment_growth.png")
NON_EPISODE_PLOT_PNG = os.path.join(OUT_DIR, f"{SHOW_SLUG}_non_episode_comment_growth.png")

DASHBOARD_HTML = os.path.join(OUT_DIR, f"dashboard_{SHOW_SLUG}.html")


# -----------------------------
# Logging
# -----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_int(x, default=0):
    try:
        return int(x)
    except Exception:
        return default


def norm_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


# -----------------------------
# Episode parsing (expanded)
# -----------------------------
EP_PATTERNS = [
    # 1x01, 1X02, 10x3 (normalize)
    re.compile(r"\b(\d{1,2})\s*[xX]\s*(\d{1,2})\b"),
    # S01E01, s1e2
    re.compile(r"\b[Ss](\d{1,2})\s*[Ee](\d{1,2})\b"),
    # "Episode 3", "Ep 3", "Ep. 3"
    re.compile(r"\b(?:episode|ep)\.?\s*(\d{1,2})\b", re.IGNORECASE),
]


def extract_episode_code(title: str) -> Optional[str]:
    """
    Returns:
      - "1x02" for season/episode patterns when available
      - "E03" for episode-only patterns (no season info)
    """
    t = title or ""
    for pat in EP_PATTERNS:
        m = pat.search(t)
        if not m:
            continue

        if pat.pattern.lower().find("[xX]") != -1 or "Ss" in pat.pattern or "Ee" in pat.pattern:
            # season/episode
            season = int(m.group(1))
            ep = int(m.group(2))
            return f"{season}x{ep:02d}"

        # episode-only
        ep_only = int(m.group(1))
        return f"E{ep_only:02d}"

    return None


def looks_like_trailer(title: str) -> bool:
    t = (title or "").lower()
    # must contain show name OR one of the query terms (loose)
    show_hit = (SHOW_NAME.lower() in t) or any(q.strip('"').lower() in t for q in QUERY_TERMS if q)
    if not show_hit:
        return False

    # trailer-ish keywords
    return any(k in t for k in TRAILER_KEYWORDS)


# -----------------------------
# Data model
# -----------------------------
@dataclass
class Post:
    id: str
    name: str
    subreddit: str
    created_utc: int
    created_iso: str
    title: str
    permalink: str
    url: str
    author: str
    score: int
    num_comments: int
    episode_code: Optional[str]
    is_trailer: bool


# -----------------------------
# HTTP helpers
# -----------------------------
def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    return session


def request_json(session: requests.Session, url: str, params: dict, max_retries: int = 5) -> dict:
    for attempt in range(1, max_retries + 1):
        r = session.get(url, params=params, timeout=30, allow_redirects=True)

        if r.status_code == 429:
            retry_after = r.headers.get("Retry-After")
            wait = int(retry_after) if retry_after and retry_after.isdigit() else min(60, 2 ** attempt)
            logging.warning(f"HTTP 429 rate-limited. Waiting {wait}s (attempt {attempt}/{max_retries})...")
            time.sleep(wait)
            continue

        if 500 <= r.status_code < 600:
            wait = min(60, 2 ** attempt)
            logging.warning(f"HTTP {r.status_code}. Waiting {wait}s (attempt {attempt}/{max_retries})...")
            time.sleep(wait)
            continue

        r.raise_for_status()

        ct = (r.headers.get("Content-Type") or "").lower()
        if "json" not in ct:
            raise ValueError(f"Expected JSON but got Content-Type={ct}. Final URL: {r.url}")

        return r.json()

    raise RuntimeError("Failed after retries (rate-limited or server errors).")


# -----------------------------
# Reddit fetch (multi-subreddit, multi-term)
# -----------------------------
def fetch_search_posts() -> List[Post]:
    session = build_session()
    posts: List[Post] = []
    seen_ids: set[str] = set()

    for sr in SUBREDDITS:
        search_url = f"https://www.reddit.com/r/{sr}/search.json"

        for term in QUERY_TERMS:
            q = term
            params = {
                "q": q,
                "restrict_sr": 1,
                "sort": SORT,
                "t": TIME_FILTER,
                "limit": LIMIT,
                "raw_json": 1,
            }

            logging.info(f"Searching r/{sr} for {q!r} (limit={LIMIT}, sort={SORT}, t={TIME_FILTER})")
            data = request_json(session, search_url, params=params)

            children = (data.get("data") or {}).get("children") or []
            for ch in children:
                d = ch.get("data") or {}
                pid = d.get("id")
                if not pid or pid in seen_ids:
                    continue
                seen_ids.add(pid)

                created_utc = safe_int(d.get("created_utc"), 0)
                created_iso = datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat() if created_utc else ""

                title = d.get("title") or ""
                ep = extract_episode_code(title)
                trailer = looks_like_trailer(title)

                posts.append(Post(
                    id=pid,
                    name=d.get("name") or f"t3_{pid}",
                    subreddit=d.get("subreddit") or sr,
                    created_utc=created_utc,
                    created_iso=created_iso,
                    title=norm_spaces(title),
                    permalink="https://www.reddit.com" + (d.get("permalink") or ""),
                    url=d.get("url") or "",
                    author=d.get("author") or "",
                    score=safe_int(d.get("score"), 0),
                    num_comments=safe_int(d.get("num_comments"), 0),
                    episode_code=ep,
                    is_trailer=trailer,
                ))

    # newest first (useful default)
    posts.sort(key=lambda p: p.created_utc, reverse=True)
    logging.info(f"Found {len(posts)} unique posts across subreddits/terms.")
    return posts


# -----------------------------
# CSV writers
# -----------------------------
def write_csv(path: str, rows: List[dict], fieldnames: List[str]):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def ensure_history_header(path: str):
    if os.path.exists(path):
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "snapshot_utc", "post_id", "post_name", "subreddit",
            "episode_code", "is_episode", "is_trailer",
            "title", "permalink", "num_comments"
        ])
        w.writeheader()


def append_history(snapshot_utc: str, posts: List[Post]):
    ensure_history_header(COMMENT_HISTORY_CSV)
    with open(COMMENT_HISTORY_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "snapshot_utc", "post_id", "post_name", "subreddit",
            "episode_code", "is_episode", "is_trailer",
            "title", "permalink", "num_comments"
        ])
        for p in posts:
            w.writerow({
                "snapshot_utc": snapshot_utc,
                "post_id": p.id,
                "post_name": p.name,
                "subreddit": p.subreddit,
                "episode_code": p.episode_code or "",
                "is_episode": 1 if p.episode_code else 0,
                "is_trailer": 1 if p.is_trailer else 0,
                "title": p.title,
                "permalink": p.permalink,
                "num_comments": p.num_comments,
            })


# -----------------------------
# Selection logic
# -----------------------------
def pick_trailer(posts: List[Post]) -> Optional[Post]:
    trailers = [p for p in posts if p.is_trailer]
    if not trailers:
        return None
    # pick “most discussed”, tiebreaker by score
    return sorted(trailers, key=lambda p: (p.num_comments, p.score), reverse=True)[0]


def episode_posts(posts: List[Post]) -> List[Post]:
    eps = [p for p in posts if p.episode_code]
    # sort episode code then created (stable)
    eps.sort(key=lambda p: (p.episode_code or "", p.created_utc))
    return eps


def pick_other_posts(posts: List[Post], n: int) -> List[Post]:
    # exclude episode + trailer
    candidates = [p for p in posts if not p.episode_code and not p.is_trailer]
    candidates = sorted(candidates, key=lambda p: (p.num_comments, p.score), reverse=True)
    return candidates[:n]


# -----------------------------
# Plotting
# -----------------------------
def make_plots():
    import matplotlib.pyplot as plt

    if not os.path.exists(COMMENT_HISTORY_CSV):
        logging.warning("No comment history yet; skipping plots. Re-run over time to build history.")
        return

    history_rows = []
    with open(COMMENT_HISTORY_CSV, "r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            history_rows.append(row)

    def parse_dt(s: str):
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return None

    by_post: Dict[str, List[Tuple[datetime, int, dict]]] = {}
    for row in history_rows:
        dt = parse_dt(row["snapshot_utc"])
        if not dt:
            continue
        post_name = row["post_name"]
        num_comments = safe_int(row["num_comments"], 0)
        by_post.setdefault(post_name, []).append((dt, num_comments, row))

    for k in list(by_post.keys()):
        by_post[k].sort(key=lambda x: x[0])

    # Episode plot
    plt.figure()
    plotted_any = False
    for post_name, series in by_post.items():
        if series[0][2].get("is_episode") != "1":
            continue
        x = [t for (t, _, __) in series]
        y = [c for (_, c, __) in series]
        label = series[0][2].get("episode_code") or post_name
        plt.plot(x, y, label=label)
        plotted_any = True

    if plotted_any:
        plt.title(f"{SHOW_NAME}: Episode threads comment counts over time")
        plt.xlabel("Snapshot time (UTC)")
        plt.ylabel("Comments")
        plt.xticks(rotation=30, ha="right")
        plt.tight_layout()
        plt.legend(loc="best", fontsize=8)
        plt.savefig(EPISODE_PLOT_PNG, dpi=150)
        plt.close()
        logging.info(f"Wrote plot: {EPISODE_PLOT_PNG}")
    else:
        plt.close()

    # Non-episode plot
    plt.figure()
    plotted_any = False
    for post_name, series in by_post.items():
        if series[0][2].get("is_episode") == "1":
            continue
        x = [t for (t, _, __) in series]
        y = [c for (_, c, __) in series]
        title = (series[0][2].get("title") or "")[:45].strip()
        label = title + ("…" if len(series[0][2].get("title") or "") > 45 else "")
        plt.plot(x, y, label=label)
        plotted_any = True

    if plotted_any:
        plt.title(f"{SHOW_NAME}: Non-episode posts comment counts over time")
        plt.xlabel("Snapshot time (UTC)")
        plt.ylabel("Comments")
        plt.xticks(rotation=30, ha="right")
        plt.tight_layout()
        plt.legend(loc="best", fontsize=7)
        plt.savefig(NON_EPISODE_PLOT_PNG, dpi=150)
        plt.close()
        logging.info(f"Wrote plot: {NON_EPISODE_PLOT_PNG}")
    else:
        plt.close()


# -----------------------------
# HTML dashboard
# -----------------------------
def write_dashboard_html(all_posts: List[Post], eps: List[Post], trailer: Optional[Post], others: List[Post]):
    def row_for(p: Post) -> str:
        ep = p.episode_code or ""
        kind = "Episode" if p.episode_code else ("Trailer" if p.is_trailer else "Other")
        return f"""
        <tr>
          <td>{kind}</td>
          <td>{p.subreddit}</td>
          <td>{ep}</td>
          <td><a href="{p.permalink}" target="_blank" rel="noopener">{p.title}</a></td>
          <td style="text-align:right">{p.num_comments}</td>
          <td style="text-align:right">{p.score}</td>
          <td>{p.created_iso}</td>
        </tr>
        """

    trailer_html = ""
    if trailer:
        trailer_html = f"""
        <h2>Official Trailer / Teaser (best match)</h2>
        <table>
          <thead><tr><th>Title</th><th>Subreddit</th><th>Comments</th><th>Score</th><th>Created (UTC)</th></tr></thead>
          <tbody>
            <tr>
              <td><a href="{trailer.permalink}" target="_blank" rel="noopener">{trailer.title}</a></td>
              <td>r/{trailer.subreddit}</td>
              <td style="text-align:right">{trailer.num_comments}</td>
              <td style="text-align:right">{trailer.score}</td>
              <td>{trailer.created_iso}</td>
            </tr>
          </tbody>
        </table>
        """

    eps_rows = "\n".join(row_for(p) for p in eps)
    others_rows = "\n".join(row_for(p) for p in others)

    # quick stats
    total_posts = len(all_posts)
    total_eps = len(eps)
    total_trailers = len([p for p in all_posts if p.is_trailer])
    total_comments = sum(p.num_comments for p in all_posts)

    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>RewindOS: {SHOW_NAME} Reddit tracker</title>
  <style>
    body {{ font-family: system-ui, Arial, sans-serif; margin: 24px; }}
    .muted {{ color: #666; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 16px 0 22px; }}
    .card {{ border: 1px solid #ddd; border-radius: 12px; padding: 12px; }}
    .kpi {{ font-size: 22px; font-weight: 700; }}
    table {{ border-collapse: collapse; width: 100%; margin: 12px 0 24px; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; vertical-align: top; }}
    th {{ background: #f6f6f6; text-align: left; }}
    img {{ max-width: 100%; border: 1px solid #ddd; border-radius: 10px; padding: 6px; }}
    code {{ background: #f6f6f6; padding: 2px 6px; border-radius: 6px; }}
  </style>
</head>
<body>
  <h1>{SHOW_NAME}: Reddit tracking</h1>
  <p class="muted">
    Subreddits: <code>{", ".join("r/" + s for s in SUBREDDITS)}</code><br/>
    Query terms: <code>{", ".join(QUERY_TERMS)}</code><br/>
    Generated: <code>{utc_now_iso()}</code> · Sort: <code>{SORT}</code> · Time filter: <code>{TIME_FILTER}</code><br/>
    Data source: Reddit public JSON search endpoint (no OAuth key).
  </p>

  <div class="grid">
    <div class="card"><div class="muted">Posts found</div><div class="kpi">{total_posts}</div></div>
    <div class="card"><div class="muted">Episode threads</div><div class="kpi">{total_eps}</div></div>
    <div class="card"><div class="muted">Trailer hits</div><div class="kpi">{total_trailers}</div></div>
    <div class="card"><div class="muted">Total comments (snapshot)</div><div class="kpi">{total_comments}</div></div>
  </div>

  {trailer_html}

  <h2>Episode discussion threads detected</h2>
  <table>
    <thead>
      <tr><th>Type</th><th>Subreddit</th><th>Episode</th><th>Title</th><th>Comments</th><th>Score</th><th>Created (UTC)</th></tr>
    </thead>
    <tbody>
      {eps_rows if eps_rows else "<tr><td colspan='7' class='muted'>No episode threads detected by title pattern.</td></tr>"}
    </tbody>
  </table>

  <h2>Other notable posts (top by comments)</h2>
  <table>
    <thead>
      <tr><th>Type</th><th>Subreddit</th><th>Episode</th><th>Title</th><th>Comments</th><th>Score</th><th>Created (UTC)</th></tr>
    </thead>
    <tbody>
      {others_rows if others_rows else "<tr><td colspan='7' class='muted'>No additional posts selected.</td></tr>"}
    </tbody>
  </table>

  <h2>Comment growth over time</h2>
  <p class="muted">
    These plots require multiple snapshots. Re-run on a schedule (Task Scheduler / cron) to build
    <code>{os.path.basename(COMMENT_HISTORY_CSV)}</code>.
  </p>

  <h3>Episode discussions</h3>
  <img src="{os.path.basename(EPISODE_PLOT_PNG)}" alt="Episode discussion growth plot" onerror="this.style.display='none'"/>

  <h3>Non-episode posts</h3>
  <img src="{os.path.basename(NON_EPISODE_PLOT_PNG)}" alt="Non-episode growth plot" onerror="this.style.display='none'"/>

  <h2>Outputs</h2>
  <ul>
    <li><code>{os.path.basename(ALL_POSTS_CSV)}</code></li>
    <li><code>{os.path.basename(EPISODE_POSTS_CSV)}</code></li>
    <li><code>{os.path.basename(SELECTED_POSTS_CSV)}</code></li>
    <li><code>{os.path.basename(COMMENT_HISTORY_CSV)}</code> (in /data; appended each run)</li>
  </ul>
</body>
</html>
"""
    with open(DASHBOARD_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    logging.info(f"Wrote dashboard HTML: {DASHBOARD_HTML}")


# -----------------------------
# Main
# -----------------------------
def main():
    snapshot = utc_now_iso()
    posts = fetch_search_posts()

    # All posts CSV
    all_rows = [{
        "id": p.id,
        "subreddit": p.subreddit,
        "created_utc": p.created_utc,
        "created_iso": p.created_iso,
        "title": p.title,
        "episode_code": p.episode_code or "",
        "is_trailer": 1 if p.is_trailer else 0,
        "num_comments": p.num_comments,
        "score": p.score,
        "author": p.author,
        "permalink": p.permalink,
        "url": p.url,
    } for p in posts]
    write_csv(
        ALL_POSTS_CSV,
        all_rows,
        list(all_rows[0].keys()) if all_rows else
        ["id","subreddit","created_utc","created_iso","title","episode_code","is_trailer","num_comments","score","author","permalink","url"]
    )

    # Episode posts CSV
    eps = episode_posts(posts)
    eps_rows = [{
        "episode_code": p.episode_code or "",
        "subreddit": p.subreddit,
        "id": p.id,
        "created_iso": p.created_iso,
        "title": p.title,
        "num_comments": p.num_comments,
        "score": p.score,
        "permalink": p.permalink,
    } for p in eps]
    write_csv(EPISODE_POSTS_CSV, eps_rows, ["episode_code","subreddit","id","created_iso","title","num_comments","score","permalink"])

    # Selected posts CSV (Trailer + top N others)
    trailer = pick_trailer(posts)
    others = pick_other_posts(posts, OTHER_POSTS_N)

    selected = []
    if trailer:
        selected.append(trailer)
    selected.extend(others)

    sel_rows = [{
        "type": ("Trailer" if p.is_trailer else ("Episode" if p.episode_code else "Other")),
        "subreddit": p.subreddit,
        "episode_code": p.episode_code or "",
        "id": p.id,
        "created_iso": p.created_iso,
        "title": p.title,
        "num_comments": p.num_comments,
        "score": p.score,
        "permalink": p.permalink,
    } for p in selected]
    write_csv(SELECTED_POSTS_CSV, sel_rows, ["type","subreddit","episode_code","id","created_iso","title","num_comments","score","permalink"])

    # History + plots + dashboard
    append_history(snapshot, posts)
    make_plots()
    write_dashboard_html(posts, eps, trailer, others)

    logging.info("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("FAILED run")
        raise
