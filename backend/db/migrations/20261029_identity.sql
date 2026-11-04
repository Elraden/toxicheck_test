CREATE TABLE IF NOT EXISTS public.users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email text UNIQUE,
    display_name text,
    role text NOT NULL DEFAULT 'user',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS account_kind text NOT NULL DEFAULT 'guest'
    CHECK (account_kind IN ('guest', 'account'));
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS login text;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS password_hash text;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;
CREATE UNIQUE INDEX IF NOT EXISTS users_login_unique ON public.users (lower(login)) WHERE login IS NOT NULL;
UPDATE public.users SET account_kind = 'account' WHERE role = 'admin' AND account_kind = 'guest';

CREATE TABLE IF NOT EXISTS public.user_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    token_hash text NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    csrf_token text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('guest', 'admin')),
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz
);
CREATE INDEX IF NOT EXISTS user_sessions_user_idx ON public.user_sessions(user_id);
CREATE INDEX IF NOT EXISTS user_sessions_expiry_idx ON public.user_sessions(expires_at);

CREATE TABLE IF NOT EXISTS public.auth_rate_limits (
    key text PRIMARY KEY,
    attempts integer NOT NULL,
    reset_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_rate_limits_expiry_idx ON public.auth_rate_limits(reset_at);

CREATE TABLE IF NOT EXISTS public.user_preferences (
    user_id uuid PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
    diet_type text,
    options jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.user_preferences ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 0;
ALTER TABLE public.user_preferences ADD COLUMN IF NOT EXISTS local_imported_at timestamptz;

CREATE TABLE IF NOT EXISTS public.user_ingredient_exclusions (
    user_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    ingredient_id uuid NOT NULL REFERENCES catalog.ingredients(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, ingredient_id)
);
-- Old scaffolding referenced public.ingredients. Do not discard or guess at existing user data.
DO $$
DECLARE old_fk record;
BEGIN
    FOR old_fk IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'public.user_ingredient_exclusions'::regclass AND contype = 'f'
          AND confrelid = to_regclass('public.ingredients')
    LOOP
        IF EXISTS (SELECT 1 FROM public.user_ingredient_exclusions) THEN
            RAISE EXCEPTION 'Legacy exclusions require an explicit ingredient ID migration';
        END IF;
        EXECUTE format('ALTER TABLE public.user_ingredient_exclusions DROP CONSTRAINT %I', old_fk.conname);
        ALTER TABLE public.user_ingredient_exclusions ADD CONSTRAINT exclusions_catalog_fk
            FOREIGN KEY (ingredient_id) REFERENCES catalog.ingredients(id) ON DELETE RESTRICT;
    END LOOP;
END $$;

ALTER TABLE decision_tree.user_corrections ADD COLUMN IF NOT EXISTS user_id uuid
    REFERENCES public.users(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS user_corrections_user_idx ON decision_tree.user_corrections(user_id);
