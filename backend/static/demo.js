/**
 * LiftBot — Demo page JS
 * Scroll reveal + scenario card interaction + URL deep-linking
 * (e.g. /demo/?scenario=sales auto-selects the matching card)
 */
(function () {
  "use strict";

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* Scroll reveal */
  var revealEls = document.querySelectorAll("[data-reveal]");
  if (revealEls.length) {
    if (reduceMotion || !("IntersectionObserver" in window)) {
      revealEls.forEach(function (el) { el.classList.add("is-visible"); });
    } else {
      var observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            observer.unobserve(entry.target);
          }
        });
      }, { threshold: 0.15, rootMargin: "0px 0px -40px 0px" });
      revealEls.forEach(function (el) { observer.observe(el); });
    }
  }

  /* Smooth scrolling for internal anchor links */
  document.querySelectorAll('a[href^="#"]').forEach(function (link) {
    link.addEventListener("click", function (event) {
      var targetId = link.getAttribute("href");
      if (!targetId || targetId === "#") return;
      var target = document.querySelector(targetId);
      if (!target) return;
      event.preventDefault();
      target.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
    });
  });

  /* Scenario card click → mark active + scroll to pre-launch panel */
  var scenarioCards = document.querySelectorAll(".dem-scenario-card");
  var panel = document.getElementById("demo-panel");
  var panelHeading = panel ? panel.querySelector(".dem-prelaunch__h2") : null;

  var SCENARIO_LABELS = {
    sales:    "AI Sales Employee",
    travel:   "AI Travel Consultant",
    shopping: "AI Shopping Assistant",
    support:  "AI Customer Support"
  };

  function activateScenario(key, shouldScroll) {
    var matchedCard = null;
    scenarioCards.forEach(function (c) {
      var isMatch = c.getAttribute("data-scenario") === key;
      c.classList.toggle("is-active", isMatch);
      if (isMatch) matchedCard = c;
    });

    if (!matchedCard) return;

    if (panelHeading && SCENARIO_LABELS[key]) {
      panelHeading.textContent =
        SCENARIO_LABELS[key] + " is currently available through early access.";
    }

    if (shouldScroll && panel) {
      panel.classList.add("is-visible");
      panel.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "center" });
    }
  }

  scenarioCards.forEach(function (card) {
    card.addEventListener("click", function () {
      var key = card.getAttribute("data-scenario");
      activateScenario(key, true);

      // Reflect selection in the URL (so it's shareable / refresh-safe)
      var url = new URL(window.location.href);
      url.searchParams.set("scenario", key);
      window.history.replaceState({}, "", url);
    });
  });

  /* ── Deep-link support: /demo/?scenario=sales auto-selects on load ── */
  var params = new URLSearchParams(window.location.search);
  var initialScenario = params.get("scenario");
  if (initialScenario && SCENARIO_LABELS[initialScenario]) {
    activateScenario(initialScenario, true);
  }
})();