-- Phase 8: what the app published, through the official APIs, after the user's click.
-- "Never the same text on two accounts": publishing checks the texts here first.
CREATE TABLE published (
    id           INTEGER PRIMARY KEY,
    post_id      INTEGER REFERENCES posts (id) ON DELETE SET NULL,  -- the analysed post it's about
    platform     TEXT NOT NULL CHECK (platform IN ('facebook', 'instagram')),
    target       TEXT NOT NULL,  -- the Page or Instagram account it went to
    text         TEXT NOT NULL,
    media_path   TEXT,
    published_at TEXT NOT NULL,
    remote_id    TEXT
) STRICT;
