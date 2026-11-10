-- Run once in the Supabase SQL editor.
-- Row Level Security with no policies means only the server's service key can read or write.
create table if not exists kv (
  collection text not null,
  key text not null,
  value jsonb,
  updated_at timestamptz not null default now(),
  primary key (collection, key)
);

alter table kv enable row level security;
