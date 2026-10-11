/**
 * Resources org chart: supervisor -> direct reports tree, filtered by department.
 * Expects json_script #org-chart-people (id, name, department, role, supervisor_id, url, photo)
 * and #org-chart-bootstrap with data-viewer-id.
 */
(function () {
  var dataEl = document.getElementById("org-chart-people");
  var root = document.getElementById("org-chart-root");
  var deptSelect = document.getElementById("org-chart-department");
  if (!dataEl || !root || !deptSelect) return;

  var people;
  try {
    people = JSON.parse(dataEl.textContent) || [];
  } catch (e) {
    people = [];
  }
  var boot = document.getElementById("org-chart-bootstrap");
  var viewerId = boot ? parseInt(boot.getAttribute("data-viewer-id"), 10) : null;
  var countEl = document.getElementById("org-chart-count");
  var expandBtn = document.getElementById("org-chart-expand");
  var collapseBtn = document.getElementById("org-chart-collapse");

  var NO_DEPARTMENT = "__none__";
  var ALL_DEPTH_EXPANDED = 2;

  var byId = {};
  people.forEach(function (p) {
    byId[p.id] = p;
  });

  // Department from the URL (?dept=) wins over the server default so filtered views can be shared.
  var urlDept = new URLSearchParams(window.location.search).get("dept");
  if (urlDept !== null) {
    for (var i = 0; i < deptSelect.options.length; i++) {
      if (deptSelect.options[i].value === urlDept) {
        deptSelect.value = urlDept;
        break;
      }
    }
  }

  function inDepartment(p, dept) {
    if (dept === "") return true;
    if (dept === NO_DEPARTMENT) return !p.department;
    return p.department === dept;
  }

  function buildForest(dept) {
    var members = people.filter(function (p) {
      return inDepartment(p, dept);
    });
    var memberIds = {};
    members.forEach(function (p) {
      memberIds[p.id] = true;
    });
    var children = {};
    var roots = [];
    members.forEach(function (p) {
      var sup = p.supervisor_id;
      if (sup && memberIds[sup] && sup !== p.id) {
        (children[sup] = children[sup] || []).push(p);
      } else {
        roots.push(p);
      }
    });
    Object.keys(children).forEach(function (id) {
      children[id].sort(function (a, b) {
        return a.name.localeCompare(b.name);
      });
    });

    // Supervisor loops leave people unreachable from any root; promote one per loop to a root.
    var reached = {};
    function mark(p) {
      if (reached[p.id]) return;
      reached[p.id] = true;
      (children[p.id] || []).forEach(mark);
    }
    roots.forEach(mark);
    members.forEach(function (p) {
      if (!reached[p.id]) {
        roots.push(p);
        mark(p);
      }
    });

    function teamSize(p, seen) {
      seen = seen || {};
      if (seen[p.id]) return 0;
      seen[p.id] = true;
      return (children[p.id] || []).reduce(function (n, c) {
        return n + 1 + teamSize(c, seen);
      }, 0);
    }
    // Biggest teams first so the main reporting line leads; ties stay alphabetical.
    roots.sort(function (a, b) {
      return teamSize(b) - teamSize(a);
    });
    return { roots: roots, children: children, count: members.length };
  }

  function initials(name) {
    var parts = name.split(",");
    var last = (parts[0] || "").trim();
    var first = (parts[1] || "").trim();
    return ((first.charAt(0) || "") + (last.charAt(0) || "")).toUpperCase() || "?";
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function personCard(p, showDepartment, reportsTo) {
    var card = el("div", "org-chart-card" + (p.id === viewerId ? " is-viewer" : ""));
    if (p.photo) {
      var img = el("img", "org-chart-avatar");
      img.src = p.photo;
      img.alt = "";
      img.loading = "lazy";
      card.appendChild(img);
    } else {
      var badge = el("span", "org-chart-avatar org-chart-initials", initials(p.name));
      badge.setAttribute("aria-hidden", "true");
      card.appendChild(badge);
    }
    var body = el("div", "org-chart-card-body");
    var link = el("a", "org-chart-name", p.name);
    link.href = p.url;
    body.appendChild(link);
    var details = [];
    if (p.role) details.push(p.role);
    if (showDepartment && p.department) details.push(p.department);
    if (details.length) body.appendChild(el("div", "org-chart-meta", details.join(" · ")));
    if (p.id === viewerId) body.appendChild(el("div", "org-chart-meta org-chart-you", "You"));
    if (reportsTo) {
      var rt = el("div", "org-chart-meta");
      rt.appendChild(document.createTextNode("Reports to "));
      var supLink = el("a", "", reportsTo.name);
      supLink.href = reportsTo.url;
      rt.appendChild(supLink);
      body.appendChild(rt);
    }
    card.appendChild(body);
    return card;
  }

  function render() {
    var dept = deptSelect.value;
    var forest = buildForest(dept);
    var showDepartment = dept === "";
    root.textContent = "";

    if (countEl) {
      countEl.textContent = forest.count === 1 ? "1 person" : forest.count + " people";
    }
    if (!forest.count) {
      root.appendChild(el("p", "text-muted small mb-0", "No one is assigned to this department."));
      return;
    }

    var rendered = {};
    function renderNode(p, depth) {
      rendered[p.id] = true;
      var li = el("li", "org-chart-node");
      var reportsTo = null;
      if (depth === 0 && p.supervisor_id && byId[p.supervisor_id] && p.supervisor_id !== p.id) {
        reportsTo = byId[p.supervisor_id];
      }
      li.appendChild(personCard(p, showDepartment, reportsTo));

      var kids = (forest.children[p.id] || []).filter(function (c) {
        return !rendered[c.id];
      });
      if (kids.length) {
        var expanded = dept !== "" || depth < ALL_DEPTH_EXPANDED;
        var toggle = el("button", "org-chart-toggle btn btn-sm btn-outline-secondary");
        toggle.type = "button";
        toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
        toggle.setAttribute(
          "aria-label",
          (expanded ? "Hide" : "Show") + " " + kids.length + " direct report" + (kids.length === 1 ? "" : "s") + " of " + p.name
        );
        toggle.appendChild(el("span", "org-chart-toggle-icon"));
        toggle.appendChild(el("span", "", String(kids.length)));
        li.appendChild(toggle);

        var allLeaves = kids.every(function (c) {
          return !(forest.children[c.id] || []).length;
        });
        var ul = el("ul", "org-chart-level" + (allLeaves && kids.length > 2 ? " org-chart-stack" : ""));
        kids.forEach(function (c) {
          ul.appendChild(renderNode(c, depth + 1));
        });
        ul.hidden = !expanded;
        li.appendChild(ul);
      }
      return li;
    }

    var top = el("ul", "org-chart-level org-chart-top");
    forest.roots.forEach(function (p) {
      if (!rendered[p.id]) top.appendChild(renderNode(p, 0));
    });
    root.appendChild(top);
  }

  function setExpanded(toggle, expanded) {
    var list = toggle.parentNode.querySelector(":scope > ul");
    if (!list) return;
    list.hidden = !expanded;
    toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
    var label = toggle.getAttribute("aria-label") || "";
    toggle.setAttribute("aria-label", label.replace(/^(Show|Hide)/, expanded ? "Hide" : "Show"));
  }

  root.addEventListener("click", function (e) {
    var toggle = e.target.closest(".org-chart-toggle");
    if (!toggle || !root.contains(toggle)) return;
    setExpanded(toggle, toggle.getAttribute("aria-expanded") !== "true");
  });

  function setAll(expanded) {
    root.querySelectorAll(".org-chart-toggle").forEach(function (t) {
      setExpanded(t, expanded);
    });
  }
  if (expandBtn) expandBtn.addEventListener("click", function () { setAll(true); });
  if (collapseBtn) collapseBtn.addEventListener("click", function () { setAll(false); });

  deptSelect.addEventListener("change", function () {
    var url = new URL(window.location.href);
    if (deptSelect.value === "") url.searchParams.delete("dept");
    else url.searchParams.set("dept", deptSelect.value);
    history.replaceState(null, "", url.pathname + url.search + url.hash);
    render();
  });

  render();
})();
