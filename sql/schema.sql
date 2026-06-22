CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS articles (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    url          TEXT UNIQUE NOT NULL,
    title        TEXT NOT NULL,
    source       TEXT,
    category     TEXT,
    published_at TIMESTAMPTZ,
    collected_at TIMESTAMPTZ DEFAULT NOW(),
    summary      TEXT,
    importance   SMALLINT CHECK (importance BETWEEN 1 AND 5),
    tags         TEXT[],
    technologies TEXT[],
    actions      TEXT,
    raw_content  TEXT,
    embedding    vector(1536)
);

CREATE INDEX IF NOT EXISTS idx_collected  ON articles (collected_at DESC);
CREATE INDEX IF NOT EXISTS idx_importance ON articles (importance DESC);
CREATE INDEX IF NOT EXISTS idx_category   ON articles (category);
CREATE INDEX IF NOT EXISTS idx_tags       ON articles USING GIN (tags);
