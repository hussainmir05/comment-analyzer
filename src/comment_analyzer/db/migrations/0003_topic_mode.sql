-- Phase 3, step 2: a topic belongs to one analysis mode, like the analysis rows that point at it.
ALTER TABLE topics ADD COLUMN mode TEXT NOT NULL DEFAULT 'local' CHECK (mode IN ('local', 'claude'));
