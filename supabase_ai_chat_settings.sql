create table if not exists public.ai_chat_settings (
    chat_id bigint primary key,
    enabled boolean not null default true,
    prompt text not null default 'Отвечай естественно, как обычный человек. Не используй канцелярит, не начинай ответы с «Конечно» и не повторяй шаблонные фразы.',
    context_size integer not null default 10,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table public.ai_chat_settings enable row level security;

-- Бот использует Supabase service-role key на сервере, поэтому отдельные
-- публичные INSERT/UPDATE/SELECT policies здесь не нужны.

create index if not exists ai_chat_settings_chat_id_idx
    on public.ai_chat_settings(chat_id);
