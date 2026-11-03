-- Findings retain their exact task-version source outside the hash envelope.
-- Adding/recovering a source pointer changes no authored content or hashes.
ALTER TABLE goal_findings ADD COLUMN task_entry_id TEXT REFERENCES goal_entries(id);
DROP TRIGGER goal_findings_immutable;
UPDATE goal_findings SET task_entry_id=(
    SELECT source.id FROM goal_entries source JOIN goal_entries finding ON finding.id=goal_findings.entry_id
    WHERE source.goal_id=finding.goal_id AND source.kind='step' AND source.seq<finding.seq
      AND json_extract(source.content_json,'$.task_id')=goal_findings.task_id
      -- An erased later step may have changed this task. Its absent content
      -- cannot establish provenance, so keep the pointer NULL and fail closed.
      AND NOT EXISTS(SELECT 1 FROM goal_entries erased WHERE erased.goal_id=finding.goal_id
                     AND erased.kind='step' AND erased.content_json IS NULL
                     AND erased.seq>source.seq AND erased.seq<finding.seq)
    ORDER BY source.seq DESC LIMIT 1
);
CREATE TRIGGER goal_findings_immutable BEFORE UPDATE ON goal_findings
BEGIN SELECT RAISE(ABORT,'Finding task provenance is immutable'); END;
CREATE INDEX goal_findings_task_entry ON goal_findings(task_entry_id);
