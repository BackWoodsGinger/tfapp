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


def department_label(dept):
    if dept == "":
        return "All departments"
    if dept == NO_DEPARTMENT:
        return "No department"
    return dept


def build_forest(people, dept):
    """Roots and children map for the department filter (same rules as the page script)."""
    visible = visible_ids(people, dept)
    members = [p for p in people if p["id"] in visible]
    member_ids = {p["id"] for p in members}
    children = {}
    roots = []
    for person in members:
        pid = parent_id(person, member_ids)
        if pid:
            children.setdefault(pid, []).append(person)
        else:
            roots.append(person)
    for kids in children.values():
        kids.sort(key=lambda p: p["name"].casefold())

    reached = set()

    def mark(person):
        if person["id"] in reached:
            return
        reached.add(person["id"])
        for child in children.get(person["id"], []):
            mark(child)

    for root in roots:
        mark(root)
    for person in members:
        if person["id"] not in reached:
            roots.append(person)
            mark(person)

    def team_size(person, seen=None):
        seen = seen if seen is not None else set()
        if person["id"] in seen:
            return 0
        seen.add(person["id"])
        return sum(1 + team_size(child, seen) for child in children.get(person["id"], []))

    roots.sort(key=lambda p: (-team_size(p), p["name"].casefold()))
    return roots, children


def flatten_forest(roots, children):
    rows = []

    def walk(person, depth):
        row = dict(person)
        row["depth"] = depth
        rows.append(row)
        for child in children.get(person["id"], []):
            walk(child, depth + 1)

    for root in roots:
        walk(root, 0)
    return rows
