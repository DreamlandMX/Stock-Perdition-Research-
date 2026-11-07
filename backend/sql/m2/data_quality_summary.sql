CREATE OR REPLACE VIEW data_quality_summary AS
SELECT
    manifests.dataset_revision,
    COUNT(*)::BIGINT AS batch_count,
    SUM(quality.input_rows)::BIGINT AS input_rows,
    SUM(quality.valid_rows)::BIGINT AS valid_rows,
    SUM(quality.quarantined_rows)::BIGINT AS quarantined_rows,
    AVG(quality.quality_score)::DOUBLE AS average_quality_score
FROM batch_manifests AS manifests
JOIN quality_reports AS quality USING (batch_id)
GROUP BY manifests.dataset_revision
ORDER BY manifests.dataset_revision;
