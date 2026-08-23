/**
 * LiftBot — FAQ page JS
 * Accordion toggle + scroll reveal
 */
(function () {
  "use strict";

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ── Accordion ── */
  var questions = document.querySelectorAll(".faq-item__q");
  questions.forEach(function (btn) {
    btn.addEventListener("click", function () {
      var expanded = btn.getAttribute("aria-expanded") === "true";

      // Close all others (single-open accordion)
      questions.forEach(function (other) {
        other.setAttribute("aria-expanded", "false");
      });

      btn.setAttribute("aria-expanded", expanded ? "false" : "true");
    });
  });

  /* ── Scroll Reveal ── */
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
})();