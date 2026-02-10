# Star Trek: Starfleet Academy — r/television Tracker

A lightweight Python project for observing **general-audience and industry-level discussion** of *Star Trek: Starfleet Academy* on Reddit by tracking **discussion behavior**, not subscriber counts.

This tracker is intentionally scoped to **r/television**, where conversation reflects premieres, trailers, media coverage, and cultural reaction rather than deep fandom participation. It complements (but does not replace) separate trackers for Star Trek–specific subreddits.

---

## What This Project Does

This first-pass tracker focuses on *where* and *when* Starfleet Academy enters mainstream conversation.

Specifically, it:

* Searches **r/television** for posts related to *Star Trek: Starfleet Academy*
* Identifies and classifies posts into:

  * episode discussion threads (e.g. `1x03`, `S01E01`, `Episode 4`)
  * official trailers and teasers
  * high-engagement, non-episode posts (premieres, media articles, controversy)
* Captures post-level metadata:

  * comment count
  * score (net upvotes)
  * creation timestamp
  * subreddit source
* Appends comment counts to a **time-series dataset** on each run
* Generates:

  * CSV exports for offline analysis
  * line graphs showing comment growth over time
  * a local, static HTML dashboard for review

The result is a reproducible snapshot of **when Starfleet Academy becomes a cultural event**, rather than an attempt to measure fandom loyalty.

---

## What This First Pass Shows

Early runs consistently show that **r/television engagement is event-driven**:

* Trailers and first-look teasers generate short-term spikes
* The series premiere dominates total engagement
* Weekly episode discussion exists, but is fragmented and low-volume
* Conversation is often framed through media narratives, ratings, or franchise discourse

This confirms that r/television functions as a **general audience + industry sentiment space**, not a sustained episodic discussion hub.

That distinction is intentional and central to the project’s design.

---

## Why Comments Instead of Subscribers

Reddit’s visible membership counts and “active users” metrics now vary by:

* interface (old vs new Reddit)
* aggregation context
* subreddit configuration

This makes them unreliable for longitudinal analysis.

Comments, by contrast:

* represent active participation
* accumulate over time
* capture both positive and negative engagement
* remain accessible via public JSON endpoints

For cultural and media analysis, **comment growth** provides a clearer signal of attention and response than subscriber totals alone.

---

## Project Structure

```
starfleet_academy_tracker/
├─ src/
│  ├─ starfleet_academy_tracker.py
│
├─ data/
│  └─ starfleet_academy_comment_history.csv
│
├─ out/
│  ├─ starfleet_academy_all_posts.csv
│  ├─ starfleet_academy_episode_posts.csv
│  ├─ starfleet_academy_selected_posts.csv
│  ├─ starfleet_academy_episode_comment_growth.png
│  ├─ starfleet_academy_non_episode_comment_growth.png
│  └─ dashboard_starfleet_academy.html
│
├─ logs/
│  └─ starfleet_academy_tracker.log
│
├─ README.md
├─ requirements.txt
└─ .gitignore
```

---

## Requirements

* Python **3.11** or newer

Install dependencies with:

```bash
pip install -r requirements.txt
```

---

## How to Run

From the project root:

```bash
python src/starfleet_academy_tracker.py
```

The script will:

* fetch current Reddit data
* append to the comment history file
* regenerate CSVs, plots, and the HTML dashboard

Open the dashboard locally:

```text
out/dashboard_starfleet_academy.html
```

(No web server required.)

---

## Notes on Data Use

* Uses **only Reddit’s public JSON search endpoints**
* No API keys or authentication required
* Designed for **infrequent polling** (6–12 hours recommended)
* Comment trends become meaningful over repeated runs
* Absence of discussion is treated as a signal, not a failure

---

## Relationship to Other Trackers

This tracker is intentionally limited to **r/television**.

Separate trackers exist (or are planned) for:

* r/startrek (general fandom)
* r/DaystromInstitute (hard science and canon analysis)

Together, these dashboards map **different layers of audience attention**, rather than blending them into a single, misleading metric.

---

Part of the **RewindOS** project — tracking cultural signals where traditional audience metrics fall short.

