"""ML specialist's heuristic score (2026-11-10), independent of storage.
The app retains its strict E-code/subtype parser and existing tokenizer.
No trained decision-tree artifact was supplied with this version.
"""
from app.services.normalization import normalize_e_code
MATCH_THRESHOLD = 0.55
UNCERTAIN_LOW = 0.40
UNCERTAIN_HIGH = 0.70

def extract_e_code(token):
    code = normalize_e_code(token)
    return code.lower() if code else None

def levenshtein_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev_row = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr_row = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr_row[j] = min(prev_row[j] + 1, curr_row[j - 1] + 1, prev_row[j - 1] + cost)
        prev_row = curr_row
    return prev_row[-1]

def damerau_levenshtein_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    len_a, len_b = (len(a), len(b))
    d = [[0] * (len_b + 2) for _ in range(len_a + 2)]
    max_dist = len_a + len_b
    d[0][0] = max_dist
    for i in range(len_a + 1):
        d[i + 1][0] = max_dist
        d[i + 1][1] = i
    for j in range(len_b + 1):
        d[0][j + 1] = max_dist
        d[1][j + 1] = j
    last_row = {}
    for i in range(1, len_a + 1):
        last_col = 0
        for j in range(1, len_b + 1):
            i1 = last_row.get(b[j - 1], 0)
            j1 = last_col
            cost = 0 if a[i - 1] == b[j - 1] else 1
            if cost == 0:
                last_col = j
            d[i + 1][j + 1] = min(d[i][j] + cost, d[i + 1][j] + 1, d[i][j + 1] + 1, d[i1][j1] + (i - i1 - 1) + 1 + (j - j1 - 1))
        last_row[a[i - 1]] = i
    return d[len_a + 1][len_b + 1]

def char_bigrams(s: str) -> set:
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}

def jaccard_bigram(a: str, b: str) -> float:
    ba, bb = (char_bigrams(a), char_bigrams(b))
    if not ba and (not bb):
        return 1.0
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)

def match_score(token: str, candidate: str) -> float:
    token_ecode = extract_e_code(token)
    candidate_ecode = extract_e_code(candidate)
    if token_ecode and candidate_ecode:
        return 1.0 if token_ecode == candidate_ecode else 0.0
    if not token or not candidate:
        return 0.0
    max_len = max(len(token), len(candidate))
    lev_sim = 1 - levenshtein_distance(token, candidate) / max_len
    dam_sim = 1 - damerau_levenshtein_distance(token, candidate) / max_len
    jaccard = jaccard_bigram(token, candidate)
    len_diff_penalty = 1 - min(abs(len(token) - len(candidate)) / max_len, 1.0)
    first_char_bonus = 0.05 if token[:1] == candidate[:1] else 0.0
    score = 0.35 * lev_sim + 0.3 * dam_sim + 0.25 * jaccard + 0.1 * len_diff_penalty + first_char_bonus
    return min(score, 1.0)

def is_uncertain(score: float) -> bool:
    return UNCERTAIN_LOW <= score <= UNCERTAIN_HIGH
