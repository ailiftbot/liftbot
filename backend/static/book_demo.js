/**
 * Book a Demo Modal — LiftBot
 * Handles open/close, validation, submission (email + Google Sheet).
 */
(function () {
  'use strict';

  /* ── Grab DOM elements ── */
  var overlay  = document.getElementById('bdmOverlay');
  var modal    = document.getElementById('bdmModal');
  var form     = document.getElementById('bdmForm');
  var formWrap = document.getElementById('bdmFormWrap');
  var resultSuccess = document.getElementById('bdmResultSuccess');
  var resultError   = document.getElementById('bdmResultError');
  var globalErr     = document.getElementById('bdmGlobalError');
  var submitBtn     = document.getElementById('bdmSubmit');

  if (!overlay || !modal) return;

  /* ── Open / Close helpers ── */
  function openModal() {
    overlay.classList.add('open');
    modal.classList.add('open');
    document.body.style.overflow = 'hidden';
    // Reset to form view
    if (formWrap)      formWrap.style.display = '';
    if (resultSuccess) resultSuccess.classList.remove('show');
    if (resultError)   resultError.classList.remove('show');
    if (globalErr)     globalErr.classList.remove('show');
    clearErrors();
    if (form) form.reset();
  }

  function closeModal() {
    overlay.classList.remove('open');
    modal.classList.remove('open');
    document.body.style.overflow = '';
  }

  /* ── Bind all triggers ── */
  document.querySelectorAll('[data-bdm-open]').forEach(function (el) {
    el.addEventListener('click', function (e) {
      e.preventDefault();
      openModal();
    });
  });

  // Close triggers
  overlay.addEventListener('click', closeModal);
  document.querySelectorAll('[data-bdm-close]').forEach(function (el) {
    el.addEventListener('click', closeModal);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && modal.classList.contains('open')) closeModal();
  });

  /* ── Validation ── */
  function clearErrors() {
    document.querySelectorAll('.bdm-field.has-error').forEach(function (f) {
      f.classList.remove('has-error');
    });
  }

  function setError(fieldId, msg) {
    var wrap = document.getElementById(fieldId);
    if (!wrap) return;
    wrap.classList.add('has-error');
    var errEl = wrap.querySelector('.bdm-error-msg');
    if (errEl) errEl.textContent = msg;
  }

  function validateForm() {
    clearErrors();
    var ok = true;

    var firstName = (form.first_name.value || '').trim();
    var lastName  = (form.last_name.value || '').trim();
    var email     = (form.work_email.value || '').trim();
    var phone     = (form.phone.value || '').trim();
    var product   = form.product.value;

    if (!firstName) { setError('bdmFieldFirstName', 'First name is required.'); ok = false; }
    if (!lastName)  { setError('bdmFieldLastName', 'Last name is required.'); ok = false; }

    if (!email) {
      setError('bdmFieldEmail', 'Email is required.');
      ok = false;
    } else {
      // Basic email regex
      var emailRe = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      if (!emailRe.test(email)) { setError('bdmFieldEmail', 'Please enter a valid email address.'); ok = false; }
    }

    if (!phone) {
      setError('bdmFieldPhone', 'Phone number is required.');
      ok = false;
    } else {
      var digits = phone.replace(/\D/g, '');
      if (digits.length < 6 || digits.length > 15) {
        setError('bdmFieldPhone', 'Enter a valid phone number (6–15 digits).');
        ok = false;
      }
    }

    if (!product) { setError('bdmFieldProduct', 'Please select a product.'); ok = false; }

    return ok;
  }

  /* ── Form Submission ── */
  if (form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      if (!validateForm()) return;

      // Gather data
      var payload = {
        first_name:   form.first_name.value.trim(),
        last_name:    form.last_name.value.trim(),
        work_email:   form.work_email.value.trim(),
        country_code: form.country_code.value,
        phone:        form.phone.value.trim(),
        product:      form.product.value,
        message:      (form.message.value || '').trim(),
      };

      // Loading state
      submitBtn.disabled = true;
      submitBtn.classList.add('loading');
      if (globalErr) globalErr.classList.remove('show');

      fetch('/api/book-demo/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      .then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok, data: data };
        });
      })
      .then(function (result) {
        submitBtn.disabled = false;
        submitBtn.classList.remove('loading');

        if (result.ok && result.data.status === 'success') {
          // Show success
          formWrap.style.display = 'none';
          if (resultSuccess) {
            var msgEl = resultSuccess.querySelector('p');
            if (msgEl) msgEl.textContent = result.data.message;
            resultSuccess.classList.add('show');
          }
        } else {
          // Show validation errors or server error
          if (result.data.errors) {
            // Field-level errors from Django
            var fieldMap = {
              first_name: 'bdmFieldFirstName',
              last_name: 'bdmFieldLastName',
              work_email: 'bdmFieldEmail',
              phone: 'bdmFieldPhone',
              product: 'bdmFieldProduct',
              country_code: 'bdmFieldCountry',
              message: 'bdmFieldMessage',
            };
            for (var key in result.data.errors) {
              if (result.data.errors.hasOwnProperty(key) && fieldMap[key]) {
                var msgs = result.data.errors[key];
                var errText = (msgs[0] && msgs[0].message) || 'Invalid value.';
                setError(fieldMap[key], errText);
              }
            }
          } else {
            // Global error
            if (globalErr) {
              globalErr.textContent = result.data.message || 'Something went wrong. Please try again.';
              globalErr.classList.add('show');
            }
          }
        }
      })
      .catch(function () {
        submitBtn.disabled = false;
        submitBtn.classList.remove('loading');
        if (globalErr) {
          globalErr.textContent = 'Network error. Please check your connection and try again.';
          globalErr.classList.add('show');
        }
      });
    });
  }

  /* ── "Close" result buttons ── */
  document.querySelectorAll('[data-bdm-reset]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      closeModal();
    });
  });
})();
