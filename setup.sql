-- Run once in Supabase: SQL Editor > New query > paste > Run
create table if not exists iso_users (
  email text primary key,
  plan text not null default 'free',
  monthly_limit int,            -- null = use the app default
  blocked boolean not null default false,
  created_at timestamptz not null default now()
);
create table if not exists iso_reads (
  id bigint generated always as identity primary key,
  email text not null references iso_users(email),
  status text not null default 'pending',   -- pending | ok | failed
  at timestamptz not null default now()
);
create index if not exists iso_reads_email_at on iso_reads(email, at);
-- Lock the tables so only your server key can touch them (the app uses the service key).
alter table iso_users enable row level security;
alter table iso_reads enable row level security;

-- Teams (added with billing). Run this block too.
create table if not exists iso_team (
  owner_email text not null,
  member_email text not null,
  primary key (owner_email, member_email)
);
alter table iso_team enable row level security;

-- Cost tracking (added later). Run this block too.
alter table iso_reads add column if not exists input_tokens int;
alter table iso_reads add column if not exists output_tokens int;
alter table iso_reads add column if not exists cost_usd numeric(10,5);
