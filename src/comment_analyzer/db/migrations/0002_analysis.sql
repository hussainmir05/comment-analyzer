-- Phase 3: analysis results. One row per comment and mode ('local' model, or 'claude' later).
-- Topics are filled in by Phase 3's second step.

CREATE TABLE topics (
    id            INTEGER PRIMARY KEY,
    post_id       INTEGER NOT NULL REFERENCES posts (id) ON DELETE CASCADE,
    label         TEXT,
    keywords      TEXT,  -- JSON list of words
    share         REAL,  -- fraction of the post's analysed comments
    summary       TEXT,
    model_version TEXT,
    created_at    TEXT NOT NULL
) STRICT;
CREATE INDEX topics_by_post ON topics (post_id);

CREATE TABLE analysis (
    comment_id     INTEGER NOT NULL REFERENCES comments (id) ON DELETE CASCADE,
    mode           TEXT NOT NULL CHECK (mode IN ('local', 'claude')),
    sentiment      TEXT CHECK (sentiment IN ('positive', 'neutral', 'negative')),
    confidence     REAL,
    low_confidence INTEGER NOT NULL DEFAULT 0 CHECK (low_confidence IN (0, 1)),
    topic_id       INTEGER REFERENCES topics (id) ON DELETE SET NULL,
    spam_flag      INTEGER NOT NULL DEFAULT 0 CHECK (spam_flag IN (0, 1)),
    model_version  TEXT NOT NULL,
    analyzed_at    TEXT NOT NULL,
    PRIMARY KEY (comment_id, mode)
) STRICT;
