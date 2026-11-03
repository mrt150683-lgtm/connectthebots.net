-- Introductions from a fingerprint or taste match, without board postings.
-- 'board' requests keep their two approved posting snapshots. 'match' requests
-- store, in the same snapshot columns, each person's own view of why they
-- match (only what both chose to share). Each person is shown only their own
-- side. Both people must still match each other when the recipient decides.
ALTER TABLE introductions ADD COLUMN source TEXT NOT NULL DEFAULT 'board' CHECK(source IN ('board','match'));
