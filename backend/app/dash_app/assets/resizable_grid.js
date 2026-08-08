// Drag-resize for the home page's 2x2 panel grid (.home-grid, defined in
// theme.css as a 3x3 track grid: panel / 6px splitter / panel, both axes).
//
// Uses event delegation on `document` rather than binding listeners to the
// splitter elements directly: Dash's client-side page router swaps the
// entire page_container subtree on navigation, so any listener attached to
// a specific node is gone once that node is replaced. Delegation looks up
// the splitter by class at event time, so it keeps working across
// navigations without needing a mount/unmount hook.
(function () {
  var MIN_TRACK_PX = 160;
  var SPLITTER_PX = 6;
  var drag = null;

  function firstTrackPx(computedTemplate) {
    return parseFloat(computedTemplate.split(" ")[0]);
  }

  document.addEventListener("mousedown", function (e) {
    var splitter = e.target.closest(".home-splitter-v, .home-splitter-h");
    if (!splitter) return;
    var grid = document.querySelector(".home-grid");
    if (!grid) return;

    var rect = grid.getBoundingClientRect();
    var style = getComputedStyle(grid);
    var isVertical = splitter.classList.contains("home-splitter-v");

    drag = {
      splitter: splitter,
      grid: grid,
      isVertical: isVertical,
      startX: e.clientX,
      startY: e.clientY,
      startFirstTrackPx: isVertical
        ? firstTrackPx(style.gridTemplateColumns)
        : firstTrackPx(style.gridTemplateRows),
      gridSizePx: isVertical ? rect.width : rect.height,
    };
    splitter.classList.add("dragging");
    document.body.style.userSelect = "none";
    e.preventDefault();
  });

  document.addEventListener("mousemove", function (e) {
    if (!drag) return;
    var maxFirstTrack = drag.gridSizePx - SPLITTER_PX - MIN_TRACK_PX;
    if (drag.isVertical) {
      var newWidth = drag.startFirstTrackPx + (e.clientX - drag.startX);
      newWidth = Math.min(Math.max(newWidth, MIN_TRACK_PX), maxFirstTrack);
      drag.grid.style.gridTemplateColumns = newWidth + "px " + SPLITTER_PX + "px 1fr";
    } else {
      var newHeight = drag.startFirstTrackPx + (e.clientY - drag.startY);
      newHeight = Math.min(Math.max(newHeight, MIN_TRACK_PX), maxFirstTrack);
      drag.grid.style.gridTemplateRows = newHeight + "px " + SPLITTER_PX + "px 1fr";
    }
  });

  document.addEventListener("mouseup", function () {
    if (!drag) return;
    drag.splitter.classList.remove("dragging");
    document.body.style.userSelect = "";
    drag = null;
  });
})();
