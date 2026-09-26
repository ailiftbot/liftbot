/**
 * LiftBot — Contact page JS
 * Handles scroll reveal and interactive AJAX submission with toast notifications
 */
(function () {
  "use strict";

  // 1. Scroll reveal
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

  // 2. Interactive Form Submission
  var form = document.querySelector(".cnt-form");
  if (!form) return;

  var submitBtn = form.querySelector(".cnt-form__submit");
  var originalBtnText = submitBtn ? submitBtn.textContent : "Contact LiftBot";

  // Toast notification helper
  function showToast(message, isError) {
    var existing = document.getElementById("lb-toast");
    if (existing) existing.remove();

    var toast = document.createElement("div");
    toast.id = "lb-toast";
    toast.style.cssText =
      "position:fixed;bottom:28px;right:28px;z-index:9999;padding:14px 22px;border-radius:12px;font-size:14px;font-weight:600;display:flex;align-items:center;gap:10px;box-shadow:0 12px 32px rgba(0,0,0,0.18);transition:all 0.3s cubic-bezier(0.16, 1, 0.3, 1);transform:translateY(20px);opacity:0;max-width:380px;" +
      (isError
        ? "background:#FEF2F2;color:#991B1B;border:1px solid #F87171;"
        : "background:#ECFDF5;color:#065F46;border:1px solid #34D399;");

    var icon = document.createElement("span");
    icon.innerHTML = isError ? "&#9888;" : "&#10003;";
    icon.style.fontSize = "16px";

    var text = document.createElement("span");
    text.textContent = message;

    toast.appendChild(icon);
    toast.appendChild(text);
    document.body.appendChild(toast);

    requestAnimationFrame(function () {
      toast.style.transform = "translateY(0)";
      toast.style.opacity = "1";
    });

    setTimeout(function () {
      toast.style.transform = "translateY(20px)";
      toast.style.opacity = "0";
      setTimeout(function () {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
      }, 350);
    }, 4500);
  }

  // Clear previous field errors
  function clearErrors() {
    form.querySelectorAll(".cnt-client-error").forEach(function (el) {
      el.remove();
    });
  }

  function addFieldError(input, msg) {
    var p = document.createElement("p");
    p.className = "cnt-client-error";
    p.style.cssText = "color:#DC2626;font-size:12px;margin:6px 0 0;";
    p.textContent = msg;
    if (input && input.parentNode) {
      input.parentNode.appendChild(p);
    }
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    clearErrors();

    var fullName = (form.querySelector("#id_full_name") || {}).value || "";
    var email = (form.querySelector("#id_email") || {}).value || "";
    var topic = (form.querySelector("#id_topic") || {}).value || "";
    var message = (form.querySelector("#id_message") || {}).value || "";

    var hasError = false;
    if (!fullName.trim()) {
      addFieldError(form.querySelector("#id_full_name"), "Please enter your name.");
      hasError = true;
    }
    if (!email.trim() || email.indexOf("@") === -1) {
      addFieldError(form.querySelector("#id_email"), "Please enter a valid email address.");
      hasError = true;
    }
    if (!topic) {
      addFieldError(form.querySelector("#id_topic"), "Please select a topic.");
      hasError = true;
    }
    if (!message.trim() || message.trim().length < 10) {
      addFieldError(form.querySelector("#id_message"), "Message must be at least 10 characters long.");
      hasError = true;
    }

    if (hasError) {
      showToast("Please fill in all required fields properly.", true);
      return;
    }

    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Sending message...";
      submitBtn.style.opacity = "0.75";
    }

    var payload = {
      full_name: fullName.trim(),
      email: email.trim(),
      topic: topic,
      message: message.trim()
    };

    var csrfToken = "";
    var csrfInput = form.querySelector("[name=csrfmiddlewaretoken]");
    if (csrfInput) csrfToken = csrfInput.value;

    fetch(form.action || window.location.href, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": csrfToken
      },
      body: JSON.stringify(payload)
    })
      .then(function (res) {
        return res.json().then(function (data) {
          return { status: res.status, ok: res.ok, data: data };
        });
      })
      .then(function (result) {
        if (result.ok && result.data.status === "success") {
          showToast(result.data.message || "Message sent successfully!", false);

          // Replace form with elegant success card
          var card = form.closest(".cnt-form-card");
          if (card) {
            card.innerHTML =
              '<div style="text-align:center;padding:48px 24px;">' +
              '<div style="width:56px;height:56px;border-radius:50%;background:#ECFDF5;display:inline-flex;align-items:center;justify-content:center;margin-bottom:16px;">' +
              '<svg width="28" height="28" viewBox="0 0 24 24" fill="none"><path d="M5 13l4 4L19 7" stroke="#10B981" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
              '</div>' +
              '<h2 style="font-size:24px;font-weight:800;color:#111827;margin-bottom:8px;">Message sent!</h2>' +
              '<p style="color:#6B7280;font-size:15px;max-width:380px;margin:0 auto 24px;line-height:1.5;">Thanks for reaching out. We have received your inquiry and our team will get back to you shortly.</p>' +
              '<a href="/" class="lb__btn lb__btn--primary" style="display:inline-block;padding:12px 28px;border-radius:999px;text-decoration:none;">Back to Home</a>' +
              '</div>';
          }
        } else {
          var errMsg = (result.data && result.data.message) || "Failed to send message. Please try again.";
          showToast(errMsg, true);
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = originalBtnText;
            submitBtn.style.opacity = "1";
          }
        }
      })
      .catch(function (err) {
        console.error("Submission error:", err);
        // Fallback to normal form submit if fetch fails
        form.submit();
      });
  });
})();