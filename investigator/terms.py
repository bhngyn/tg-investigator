"""
"Terms like this": words that are unusually frequent in a search's results compared with the whole corpus,
to suggest what else to search for.

GET /api/search/terms?<same params as /api/search>&limit=40[&scope=comments]

Method: document frequencies of the result set (first 5,000 items, in the search's own order) against document
frequencies of the whole index (an fts5vocab table on posts_fts / comments_fts), scored with the weighted
log-odds ratio with an informative Dirichlet prior (Monroe, Colaresi & Quinn 2008, "Fightin' Words"): the
corpus counts are the prior, so rare-everywhere words are not over-rewarded. score = z of that log-odds
(> ~2 is unlikely by chance). Stopwords (en/ar/ru/uk short lists), romanised `zz…` tokens, numbers, tokens under
3 characters and the query's own terms and dictionary alternatives are left out.
Each term carries its script and, when the dictionary knows it, the entry it belongs to.
"""

import math
import re
import unicodedata

from fastapi import Request

from . import db, lexicon, translit

CAP = 5000
PRIOR = 500.0          # total pseudo-count of the informative prior
MIN_IN_RESULTS = 2

STOP = frozenset("""
the and for that this with was were are has have had not but you your from they their them there what when which
who will would could should about into than then also been more most some such only over just very can its his
her him she out one all any our may new said says say after before while during because between under against
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от меня
еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам ведь там
потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже
себе под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее
кажется сейчас были куда зачем всех никогда можно при наконец два об другой хоть после над больше тот через эти
нас про всего них какая много разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой
им более всегда конечно всю между это
і й та що як він вона воно вони ми ви це той ця ці не так вже ще то але чи бо від до на по за з із зі про для
при під над без між їх його її мій твій наш ваш був була було були буде бути є тому тут там де коли тільки
дуже теж також щоб якщо коли цей цього цьому цим
في من على إلى الى عن مع هذا هذه ذلك تلك التي الذي الذين هو هي هم هن أن ان إن كان كانت يكون كل بعد قبل بين حتى
الي عن على لا ما لم لن قد ثم أو او أي اي هناك هنا كما مثل عند عندما لكن بل إذا اذا فقط أيضا ايضا
com www http https html org net 
""".split())

_TOKEN = db._TOKEN
_ROMAN = translit.ROMAN_PREFIX


def _fold(tok):
    """Same folding the FTS tokenizer applies (unicode61 remove_diacritics 2), so corpus lookups line up."""
    return "".join(c for c in unicodedata.normalize("NFD", tok) if unicodedata.category(c) != "Mn")


def tokens(text):
    out = set()
    for t in _TOKEN.findall(db.normalize(text or "")):
        for w in t.split("_"):
            w = _fold(w)
            if w:
                out.add(w)
    return out


# lexicon's frequent ru/uk/ar words (которые, сегодня, كان…) carry no signal about a result set either
_COMMON = getattr(lexicon, "COMMON_WORDS", frozenset()) | getattr(lexicon, "STOPWORDS", frozenset())


def _skip(w, own):
    if len(w) < 3 or w in STOP or w in _COMMON or w.startswith(_ROMAN) or w[0].isdigit() or sum(c.isdigit() for c in w) * 2 >= len(w):
        return True
    if w in own or any(len(o) >= 4 and w.startswith(o) for o in own):
        return True
    return False


def register(app, ctx):
    q, q1, con = ctx.q, ctx.q1, ctx.con

    def vocab(scope):
        name = "comments_fts" if scope == "comments" else "posts_fts"
        con.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {name}_vocab USING fts5vocab({name}, 'row')")
        return f"{name}_vocab"

    _bg = {}

    def background(scope, table):
        """(total docs, total term-doc pairs) of the corpus, cached against the index size."""
        n = q1("SELECT COUNT(*) n FROM " + ("comments WHERE is_mirror = 0" if scope == "comments" else "posts"))["n"]
        if _bg.get(scope, (None,))[0] != n:
            _bg[scope] = (n, q1(f"SELECT COALESCE(SUM(doc), 0) s FROM {table}")["s"])
        return n, _bg[scope][1]

    def dict_entry(tok, d, ed):
        if d is None:
            return None
        try:
            for key in lexicon.candidate_keys([tok]):
                # only lexicon terms and well-known places (rank >= 7): a match-only alias of some village
                # (мали -> Natashyno) is not a dictionary meaning of the word
                found = [e for e in lexicon._entries_for(key, d, ed)
                         if e.get("kind") == "term" or (e.get("rank") or 0) >= lexicon.RANK_KEEP]
                if found:
                    return {"entry_id": found[0]["id"], "label": found[0]["label"]}
        except Exception:
            return None
        return None

    @app.get("/api/search/terms")
    def search_terms(request: Request):
        params = ctx.qparams(request)
        scope = "comments" if params.get("scope") == "comments" else "posts"
        limit = min(int(params.get("limit") or 40), 200)
        exp = ctx.query_expansion(params)
        table = vocab(scope)
        # texts in the search's own order (first CAP), one document each
        sub, args = ctx.subquery(params, scope, cols="text", order=True, limit=CAP)
        docs = [r["text"] for r in ctx.q(sub, args)]
        df = {}
        for text in docs:
            for w in tokens(text):
                df[w] = df.get(w, 0) + 1
        own = set()
        for t in lexicon.highlight_terms(exp, params.get("q")):
            own |= tokens(t)
        cand = [w for w, n in df.items() if n >= (MIN_IN_RESULTS if len(docs) > 10 else 1) and not _skip(w, own)]
        n_docs, n_pairs = background(scope, table)
        n_r = len(docs)
        if not cand or n_r < 2:
            return {"scope": scope, "results_used": n_r, "corpus_docs": n_docs, "terms": [],
                    "method": "log-odds with informative Dirichlet prior (Monroe et al. 2008)"}
        corpus = {}
        for i in range(0, len(cand), 500):
            chunk = cand[i:i + 500]
            for r in ctx.q(f"SELECT term, doc FROM {table} WHERE term IN ({','.join('?' * len(chunk))})", chunk):
                corpus[r["term"]] = r["doc"]
        n_res_pairs = sum(df.values())
        a0 = max(n_pairs, 1)
        scale = PRIOR / a0
        out = []
        for w in cand:
            y = df[w]
            a = max(corpus.get(w, y), y)
            alpha = a * scale + 0.01
            y_c = a - y                       # occurrences outside the result set
            n_c = max(a0 - n_res_pairs, 1)
            lo_r = math.log((y + alpha) / (n_res_pairs + PRIOR - y - alpha))
            lo_c = math.log((y_c + alpha) / (n_c + PRIOR - y_c - alpha))
            var = 1.0 / (y + alpha) + 1.0 / (y_c + alpha)
            out.append((((lo_r - lo_c) / math.sqrt(var)), w, y, a))
        out.sort(key=lambda x: (-x[0], x[1]))
        d, ed = lexicon.get_dict(), lexicon.load_edits(ctx)
        terms = [{"term": w, "in_results": y, "in_corpus": a, "score": round(z, 2), "script": translit.script_of(w),
                  "dictionary": dict_entry(w, d, ed)} for z, w, y, a in out[:limit] if z > 0]
        return {"scope": scope, "results_used": n_r, "corpus_docs": n_docs, "terms": terms,
                "method": "log-odds with informative Dirichlet prior (Monroe et al. 2008); in_results / in_corpus "
                          "are numbers of items containing the term"}
