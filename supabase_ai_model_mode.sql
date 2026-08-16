-- Run this once in Supabase SQL Editor.
-- Adds the per-chat AI model mode used by the new AI menu.

alter table public.ai_chat_settings
add column if not exists model_mode text not null default 'balanced';

alter table public.ai_chat_settings
drop constraint if exists ai_chat_settings_model_mode_check;

alter table public.ai_chat_settings
add constraint ai_chat_settings_model_mode_check
check (model_mode in ('smart', 'balanced', 'economy'));
