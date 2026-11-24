"""Prefer a locally executed encoder, falling back to term overlap on outages."""
import re
from functools import lru_cache

@lru_cache(maxsize=1)
def encoder():
    try:
        from fastembed import TextEmbedding
        return TextEmbedding("BAAI/bge-small-en-v1.5",threads=2)
    except Exception:
        return None

def rank(query,lines):
    if not lines:
        return []
    model=encoder()
    if model is not None:
        try:
            query_vector=next(model.query_embed(query[:3500]))
            vectors=list(model.passage_embed(lines))
            return [line for _,line in sorted(zip([float(query_vector@v) for v in vectors],lines),key=lambda item:-item[0])]
        except Exception:
            pass
    terms=set(re.findall(r"[\w+#-]+",query.lower()))
    return sorted(lines,key=lambda line:-len(terms.intersection(re.findall(r"[\w+#-]+",line.lower()))))
