CREATE SCHEMA IF NOT EXISTS decision_tree;

-- Anonymous annotations stay separate from trusted training examples.
CREATE TABLE IF NOT EXISTS decision_tree.user_corrections (
    id uuid PRIMARY KEY,
    request_id uuid NOT NULL,
    token_index integer NOT NULL CHECK (token_index >= 0),
    token_raw text NOT NULL,
    corrected_text text NOT NULL CHECK (length(trim(corrected_text)) BETWEEN 1 AND 500),
    input_sha256 text NOT NULL CHECK (length(input_sha256) = 64),
    source text NOT NULL DEFAULT 'user_correction',
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_user_corrections_request
    ON decision_tree.user_corrections (request_id, created_at);
