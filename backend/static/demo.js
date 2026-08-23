/**
 * LiftBot — Demo page JS
 * Scroll reveal (same pattern as industries.js) + scenario card interaction
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

  scenarioCards.forEach(function (card) {
    card.addEventListener("click", function () {
      scenarioCards.forEach(function (c) { c.classList.remove("is-active"); });
      card.classList.add("is-active");

      if (panel) {
        panel.classList.add("is-visible");
        panel.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "center" });
      }
    });
  });
})();