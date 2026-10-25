ALTER TABLE import_logs ADD COLUMN worksheet_name TEXT;
ALTER TABLE import_logs ADD COLUMN mapping_json TEXT;
ALTER TABLE import_logs ADD COLUMN backup_path TEXT;
ALTER TABLE import_logs ADD COLUMN source_path TEXT;
