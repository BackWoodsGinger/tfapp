"""Org-chart membership and parent rules used by the Resources page (mirrored in JS)."""

NO_DEPARTMENT = "__none__"
PARENT_FIELDS = ("team_lead_id", "group_lead_id", "supervisor_id")
PARENT_LABELS = {
    "team_lead_id": "Team lead",
    "group_lead_id": "Group lead",
    "supervisor_id": "Supervisor",
}


def in_department(person, dept):
    if dept == "":
        return True
    if dept == NO_DEPARTMENT:
        return not person.get("department")
    return person.get("department") == dept


def parent_field(person, member_ids):
    """Closest reporting link that is in ``member_ids``: team lead, then group lead, then supervisor."""
    pid = person["id"]
    for field in PARENT_FIELDS:
        parent = person.get(field)
        if parent and parent != pid and parent in member_ids:
            return field
    return None


def parent_id(person, member_ids):
    field = parent_field(person, member_ids)
    return person.get(field) if field else None


def visible_ids(people, dept):
    """
    People shown for a department filter.

    - Matching department (or everyone when ``dept`` is empty).
    - Their supervisor / group-lead / team-lead ancestors, so a lead in another
      department still appears above their team.
    """
    by_id = {p["id"]: p for p in people}
    seed = {p["id"] for p in people if in_department(p, dept)}
    visible = set(seed)

    queue = list(seed)
    while queue:
        current = by_id[queue.pop()]
        for field in PARENT_FIELDS:
            ancestor = current.get(field)
            if ancestor and ancestor in by_id and ancestor not in visible:
                visible.add(ancestor)
                queue.append(ancestor)

    return visible
