/**
 * LiftBot — Early Access page JS
 * Scroll reveal (same pattern as industries.js) + 2-step form logic
 */
(function () {
  "use strict";

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

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

  // ── 2-step Early Access form ──
  var form = document.getElementById("eaForm");
  if (form) {
    var indicators = document.querySelectorAll("[data-step-indicator]");
    var nextBtn = form.querySelector(".ea-form__next");
    var backBtn = form.querySelector(".ea-form__back");

    var showStep = function (n) {
      form.querySelectorAll(".ea-form__step").forEach(function (s) {
        s.hidden = s.dataset.step !== String(n);
      });
      indicators.forEach(function (i) {
        i.classList.toggle("active", i.dataset.stepIndicator === String(n));
      });
    };

    if (nextBtn) {
      nextBtn.addEventListener("click", function () {
        var step1 = form.querySelector('[data-step="1"]');
        var invalid = step1.querySelector(":invalid");
        if (invalid) { invalid.reportValidity(); return; }
        showStep(2);
      });
    }
    if (backBtn) {
      backBtn.addEventListener("click", function () { showStep(1); });
    }

    showStep(1);
  }
})();