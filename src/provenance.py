"""Explicit, transitive input provenance. A trigger ID is not a source list."""


def provenance(*, events=(), records=()):
    ids = set()
    complete = True
    for event in events:
        if isinstance(event.get("id"), str) and event["id"]:
            ids.add(event["id"])
        else:
            complete = False
    for record in records:
        proof = record.get("provenance", {})
        sources = proof.get("event_ids") if isinstance(proof, dict) else None
        valid = isinstance(sources, list) and all(isinstance(value, str) and value for value in sources)
        if valid:
            ids.update(sources)
        if not valid or proof.get("version") != 1 or proof.get("complete") is not True:
            complete = False
    return {"version": 1, "event_ids": sorted(ids), "complete": complete}
