/**
 * Resources events calendar: year / month / week / day views.
 * Expects a script tag with id resources-calendar-bootstrap and data-feed-url (same-origin JSON).
 */
(function () {
  var boot = document.getElementById("resources-calendar-bootstrap");
  if (!boot) return;
  var feedUrl = boot.getAttribute("data-feed-url");
  if (!feedUrl) return;

  var el = document.getElementById("resources-calendar-root");
  var titleEl = document.getElementById("resources-calendar-title");
  var viewYear = document.getElementById("resources-cal-view-year");
  var viewMonth = document.getElementById("resources-cal-view-month");
  var viewWeek = document.getElementById("resources-cal-view-week");
  var viewDay = document.getElementById("resources-cal-view-day");
  var prevBtn = document.getElementById("resources-cal-prev");
  var nextBtn = document.getElementById("resources-cal-next");
  var todayBtn = document.getElementById("resources-cal-today");
  if (!el || !titleEl) return;

  var mode = "month";
  var cursor = new Date();
  cursor.setHours(12, 0, 0, 0);
  var events = [];

  function pad(n) {
    return n < 10 ? "0" + n : String(n);
  }
  function isoDate(d) {
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  }
  function parseStart(s) {
    if (!s) return null;
    if (s.length <= 10) {
      var p = s.split("-");
      return new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1, parseInt(p[2], 10), 12, 0, 0, 0);
    }
    var d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }
  function sameDay(a, b) {
    return a && b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }
  function eventsForDay(day) {
    return events.filter(function (ev) {
      return sameDay(ev.startDate, day);
    });
  }
  function startOfWeek(d) {
    var x = new Date(d);
    var wd = (x.getDay() + 6) % 7;
    x.setDate(x.getDate() - wd);
    x.setHours(12, 0, 0, 0);
    return x;
  }
  function addDays(d, n) {
    var x = new Date(d);
    x.setDate(x.getDate() + n);
    return x;
  }
  function monthName(m) {
    return [
      "January", "February", "March", "April", "May", "June",
      "July", "August", "September", "October", "November", "December",
    ][m];
  }

  function setMode(m) {
    mode = m;
    [viewYear, viewMonth, viewWeek, viewDay].forEach(function (btn) {
      if (!btn) return;
      btn.classList.remove("active");
      if (btn.getAttribute("data-cal-mode") === m) btn.classList.add("active");
    });
    render();
  }

  function step(delta) {
    if (mode === "day") cursor = addDays(cursor, delta);
    else if (mode === "week") cursor = addDays(cursor, delta * 7);
    else if (mode === "month") {
      cursor = new Date(cursor.getFullYear(), cursor.getMonth() + delta, 1, 12, 0, 0, 0);
    } else if (mode === "year") cursor = new Date(cursor.getFullYear() + delta, 0, 1, 12, 0, 0, 0);
    render();
  }

  function renderTitle() {
    if (mode === "day") titleEl.textContent = cursor.toLocaleDateString(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" });
    else if (mode === "week") {
      var w0 = startOfWeek(cursor);
      var w6 = addDays(w0, 6);
      titleEl.textContent =
        monthName(w0.getMonth()) + " " + w0.getDate() + " – " + monthName(w6.getMonth()) + " " + w6.getDate() + ", " + w0.getFullYear();
    } else if (mode === "month") titleEl.textContent = monthName(cursor.getMonth()) + " " + cursor.getFullYear();
    else titleEl.textContent = String(cursor.getFullYear());
  }

  function renderDayList(day) {
    var list = eventsForDay(day);
    if (!list.length) return '<p class="small text-muted mb-0">No events.</p>';
    var html = '<ul class="list-unstyled small mb-0">';
    list.forEach(function (ev) {
      html +=
        '<li class="mb-1"><a href="' +
        ev.url +
        '">' +
        escapeHtml(ev.title) +
        "</a>" +
        (ev.allDay ? "" : " · " + escapeHtml(ev.timeLabel || "")) +
        "</li>";
    });
    html += "</ul>";
    return html;
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");
  }

  function render() {
    renderTitle();
    var html = "";
    if (mode === "year") {
      for (var m = 0; m < 12; m++) {
        var monthStart = new Date(cursor.getFullYear(), m, 1, 12, 0, 0, 0);
        var monthEnd = new Date(cursor.getFullYear(), m + 1, 0, 12, 0, 0, 0);
        var inMonth = events.filter(function (ev) {
          return ev.startDate >= monthStart && ev.startDate <= monthEnd;
        });
        html += '<div class="col-12 col-md-6 col-lg-4 mb-3">';
        html += '<div class="border rounded p-2 h-100"><h6 class="small text-uppercase text-muted mb-2">' + monthName(m) + "</h6>";
        if (!inMonth.length) html += '<p class="small text-muted mb-0">No events.</p>';
        else {
          html += '<ul class="list-unstyled small mb-0">';
          inMonth.forEach(function (ev) {
            html +=
              '<li class="mb-1"><a href="' +
              ev.url +
              '">' +
              escapeHtml(ev.title) +
              "</a> · " +
              ev.startDate.getDate() +
              "</li>";
          });
          html += "</ul>";
        }
        html += "</div></div>";
      }
      el.innerHTML = '<div class="row">' + html + "</div>";
      return;
    }
    if (mode === "month") {
      var first = new Date(cursor.getFullYear(), cursor.getMonth(), 1, 12, 0, 0, 0);
      var start = new Date(first);
      start.setDate(1 - ((first.getDay() + 6) % 7));
      html = '<div class="table-responsive"><table class="table table-bordered table-sm align-top mb-0"><thead><tr>';
      "Mon Tue Wed Thu Fri Sat Sun".split(" ").forEach(function (d) {
        html += "<th class=\"text-center small\">" + d + "</th>";
      });
      html += "</tr></thead><tbody>";
      var d = new Date(start);
      for (var row = 0; row < 6; row++) {
        html += "<tr>";
        for (var c = 0; c < 7; c++) {
          var muted = d.getMonth() !== cursor.getMonth();
          html += '<td class="' + (muted ? "text-muted bg-opacity-10" : "") + '" style="min-height:5rem;vertical-align:top;width:14.28%;">';
          html += '<div class="small fw-semibold mb-1">' + d.getDate() + "</div>";
          html += renderDayList(d);
          html += "</td>";
          d.setDate(d.getDate() + 1);
        }
        html += "</tr>";
      }
      html += "</tbody></table></div>";
      el.innerHTML = html;
      return;
    }
    if (mode === "week") {
      var w0 = startOfWeek(cursor);
      html = '<div class="row g-2">';
      for (var i = 0; i < 7; i++) {
        var day = addDays(w0, i);
        html += '<div class="col-12 col-md">';
        html += '<div class="border rounded p-2 h-100"><div class="small fw-semibold mb-2">' + day.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }) + "</div>";
        html += renderDayList(day);
        html += "</div></div>";
      }
      html += "</div>";
      el.innerHTML = html;
      return;
    }
    html = '<div class="border rounded p-3">' + renderDayList(cursor) + "</div>";
    el.innerHTML = html;
  }

  function normalizeRows(rows) {
    events = (rows || []).map(function (row) {
      var start = parseStart(row.start);
      var timeLabel = "";
      if (start && !row.allDay && row.start && row.start.length > 10) {
        timeLabel = start.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
      }
      return {
        id: row.id,
        title: row.title || "Event",
        startDate: start,
        allDay: !!row.allDay,
        url: row.url || "#",
        timeLabel: timeLabel,
      };
    });
    render();
  }

  fetch(feedUrl, { headers: { "X-Requested-With": "XMLHttpRequest" } })
    .then(function (r) {
      return r.json();
    })
    .then(normalizeRows)
    .catch(function () {
      el.innerHTML = '<p class="text-danger small mb-0">Could not load events.</p>';
    });

  if (viewYear) viewYear.addEventListener("click", function () { setMode("year"); });
  if (viewMonth) viewMonth.addEventListener("click", function () { setMode("month"); });
  if (viewWeek) viewWeek.addEventListener("click", function () { setMode("week"); });
  if (viewDay) viewDay.addEventListener("click", function () { setMode("day"); });
  if (prevBtn) prevBtn.addEventListener("click", function () { step(-1); });
  if (nextBtn) nextBtn.addEventListener("click", function () { step(1); });
  if (todayBtn)
    todayBtn.addEventListener("click", function () {
      cursor = new Date();
      cursor.setHours(12, 0, 0, 0);
      render();
    });

  setMode("month");
})();
