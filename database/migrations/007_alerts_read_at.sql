-- KiranaSaathi AI — 007: Alerts read tracking.
-- Phase 2 alert requirements include read/unread state per alert.
alter table alerts add column if not exists read_at timestamptz;
