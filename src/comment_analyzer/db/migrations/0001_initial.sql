-- Core tables. Times are UTC ISO-8601 text ("2026-11-12T08:30:00Z"); booleans are 0/1.
-- Later phases add their own migrations (analysis and topics in Phase 3, published in Phase 8).

CREATE TABLE accounts (
    id           INTEGER PRIMARY KEY,
    platform     TEXT NOT NULL CHECK (platform IN ('facebook', 'instagram', 'tiktok')),
    label        TEXT NOT NULL,
    profile_dir  TEXT NOT NULL UNIQUE,
    status       TEXT NOT NULL DEFAULT 'new'
                 CHECK (status IN ('new', 'logged_in', 'logged_out', 'needs_attention')),
    created_at   TEXT NOT NULL,
    last_used_at TEXT
) STRICT;

CREATE TABLE posts (
    id            INTEGER PRIMARY KEY,
    platform      TEXT NOT NULL
                  CHECK (platform IN ('youtube', 'facebook', 'instagram', 'tiktok')),
    post_id       TEXT NOT NULL,
    url           TEXT NOT NULL,
    author        TEXT,  -- the post's author (a public account), never a commenter
    title         TEXT,
    published_at  TEXT,
    first_seen_at TEXT NOT NULL,
    tracked       INTEGER NOT NULL DEFAULT 0 CHECK (tracked IN (0, 1)),
    UNIQUE (platform, post_id)
) STRICT;

CREATE TABLE snapshots (
    id                INTEGER PRIMARY KEY,
    post_id           INTEGER NOT NULL REFERENCES posts (id) ON DELETE CASCADE,
    taken_at          TEXT NOT NULL,
    views             INTEGER,
    likes             INTEGER,
    comments_reported INTEGER,
    shares            INTEGER,
    saves             INTEGER,
    reactions_json    TEXT,  -- Facebook's reaction breakdown, e.g. {"Like": 120, "Love": 30}
    extra_json        TEXT   -- other platform-specific numbers
) STRICT;
CREATE INDEX snapshots_by_post ON snapshots (post_id, taken_at);

CREATE TABLE collection_runs (
    id                 INTEGER PRIMARY KEY,
    post_id            INTEGER NOT NULL REFERENCES posts (id) ON DELETE CASCADE,
    account_id         INTEGER REFERENCES accounts (id) ON DELETE SET NULL,
    started_at         TEXT NOT NULL,
    finished_at        TEXT,
    status             TEXT NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running', 'completed', 'stopped', 'blocked', 'failed')),
    sort_used          TEXT,
    comment_limit      INTEGER,
    comments_collected INTEGER NOT NULL DEFAULT 0,
    comments_reported  INTEGER,
    note               TEXT
) STRICT;
CREATE INDEX collection_runs_by_post ON collection_runs (post_id, started_at);

CREATE TABLE comments (
    id                  INTEGER PRIMARY KEY,
    post_id             INTEGER NOT NULL REFERENCES posts (id) ON DELETE CASCADE,
    platform_comment_id TEXT NOT NULL,
    parent_id           TEXT,  -- platform_comment_id of the comment this one replies to
    author_hash         TEXT,  -- keyed hash of the commenter's ID; names are never stored
    text                TEXT,  -- set to NULL when the retention period ends
    lang                TEXT,
    created_at          TEXT,
    like_count          INTEGER,
    reply_count         INTEGER,
    collected_at        TEXT NOT NULL,
    sort_used           TEXT,
    run_id              INTEGER REFERENCES collection_runs (id) ON DELETE SET NULL,
    UNIQUE (post_id, platform_comment_id)
) STRICT;
CREATE INDEX comments_by_post_time ON comments (post_id, created_at);

CREATE TABLE api_usage (
    id       INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    day      TEXT NOT NULL,  -- YouTube: the quota day, which resets at midnight Pacific Time
    units    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (platform, day)
) STRICT;

CREATE TABLE account_usage (
    account_id INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    day        TEXT NOT NULL,  -- local date, for the per-day limits in Settings
    posts      INTEGER NOT NULL DEFAULT 0,
    comments   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, day)
) STRICT;
