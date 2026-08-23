/**
 * LiftBot — Guide page JS
 * Scroll reveal + active sidebar link highlight
 */
(function () {
  "use strict";

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

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
      }, { threshold: 0.12, rootMargin: "0px 0px -40px 0px" });
      revealEls.forEach(function (el) { observer.observe(el); });
    }
  }

  /* ── Smooth scroll for sidebar anchor links ── */
  document.querySelectorAll('.gd-nav__link[href^="#"]').forEach(function (link) {
    link.addEventListener("click", function (event) {
      var target = document.querySelector(link.getAttribute("href"));
      if (!target) return;
      event.preventDefault();
      target.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
    });
  });

  /* ── Highlight active sidebar link on scroll ── */
  var groups = document.querySelectorAll(".gd-group[id]");
  var navLinks = document.querySelectorAll(".gd-nav__link");

  if (groups.length && "IntersectionObserver" in window) {
    var navObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        var link = document.querySelector('.gd-nav__link[href="#' + entry.target.id + '"]');
        if (!link) return;
        if (entry.isIntersecting) {
          navLinks.forEach(function (l) { l.style.color = ""; l.style.fontWeight = ""; });
          link.style.color = "#4b3aff";
          link.style.fontWeight = "700";
        }
      });
    }, { rootMargin: "-20% 0px -70% 0px" });

    groups.forEach(function (g) { navObserver.observe(g); });
  }
})();