/**
 * Confirm-before-send modal for bulk email forms.
 *
 * Any form with the class "js-email-send-confirm" is intercepted on
 * submit: instead of sending immediately, a summary modal (see
 * templates/programs/_email_send_confirm_modal.html) shows the selected
 * audience, outgoing sender address, subject, and a message preview. The
 * send button stays disabled until the "written on behalf of" checkbox
 * is ticked, and only then is the form actually submitted.
 *
 * Field names understood (whichever are present on the form):
 *   recipient_groups, statuses  - checkbox groups summarised as audience
 *   recipient_filter / student  - balance-sheet audience select(s)
 *   program                     - shown as part of the audience when set
 *   test_email                  - flags the send as test-only
 *   from_account                - sender dropdown
 *   subject                     - subject line
 *   #id_body                    - hidden rich-text body used for preview
 */
(function () {
  var MODAL_ID = 'emailSendConfirmModal';

  function init() {
    if (!document.getElementById(MODAL_ID)) return;
    if (typeof window.bootstrap === 'undefined') return;
    var forms = document.querySelectorAll('form.js-email-send-confirm');
    Array.prototype.forEach.call(forms, function (form) {
      bind(form, document.getElementById(MODAL_ID));
    });
  }

  function bind(form, modalEl) {
    var modal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
    var ack = modalEl.querySelector('#email-confirm-on-behalf');
    var sendBtn = modalEl.querySelector('#email-confirm-send');

    function resetAcknowledgment() {
      ack.checked = false;
      sendBtn.disabled = true;
    }

    ack.addEventListener('change', function () {
      sendBtn.disabled = !ack.checked;
    });

    form.addEventListener(
      'submit',
      function (e) {
        if (form.dataset.emailConfirmed === 'true') {
          // Second pass: user already confirmed in the modal.
          delete form.dataset.emailConfirmed;
          return;
        }
        e.preventDefault();
        e.stopImmediatePropagation();
        populate(modalEl, form);
        resetAcknowledgment();
        modal.show();
      },
      true
    );

    sendBtn.addEventListener('click', function () {
      if (!ack.checked) return;
      form.dataset.emailConfirmed = 'true';
      modal.hide();
      if (typeof form.requestSubmit === 'function') {
        form.requestSubmit();
      } else {
        form.submit();
      }
    });
  }

  function cleanText(text) {
    return String(text || '').replace(/\s+/g, ' ').trim();
  }

  function optionLabel(select) {
    if (!select) return '';
    var opt = select.selectedOptions && select.selectedOptions[0];
    return opt ? cleanText(opt.textContent) : '';
  }

  function checkedLabels(form, name) {
    var labels = [];
    var inputs = form.querySelectorAll('input[name="' + name + '"]:checked');
    Array.prototype.forEach.call(inputs, function (input) {
      var label = input.closest('label');
      var text = label ? cleanText(label.textContent) : input.value;
      if (!text) text = input.value;
      if (labels.indexOf(text) === -1) labels.push(text);
    });
    return labels;
  }

  function collectAudienceParts(form) {
    var parts = [];

    var programSelect = form.querySelector('select[name="program"]');
    if (programSelect && programSelect.value) {
      parts.push('Program: ' + optionLabel(programSelect));
    }

    Array.prototype.forEach.call(
      checkedLabels(form, 'recipient_groups').concat(checkedLabels(form, 'statuses')),
      function (label) {
        parts.push(label);
      }
    );

    var filter = form.querySelector('select[name="recipient_filter"]');
    if (filter && filter.value) {
      var text = optionLabel(filter);
      if (filter.value === 'individual') {
        var student = optionLabel(form.querySelector('select[name="student"]'));
        if (student) text += ': ' + student;
      }
      parts.push(text);
    }

    var testEmail = form.querySelector('input[name="test_email"]');
    var testValue = testEmail ? cleanText(testEmail.value) : '';
    if (testValue) {
      // Test sends bypass the normal audience entirely; surface that first.
      parts.unshift('TEST ONLY — just you at ' + testValue);
    }

    return parts;
  }

  function renderAudience(el, parts) {
    el.textContent = '';
    if (!parts.length) {
      var none = document.createElement('em');
      none.className = 'text-muted';
      none.textContent = '(no recipients selected)';
      el.appendChild(none);
      return;
    }
    var list = document.createElement('ul');
    list.className = 'mb-0 ps-3';
    parts.forEach(function (part) {
      var li = document.createElement('li');
      li.textContent = part;
      list.appendChild(li);
    });
    el.appendChild(list);
  }

  function populate(modalEl, form) {
    renderAudience(modalEl.querySelector('[data-email-confirm-audience]'), collectAudienceParts(form));

    var senderEl = modalEl.querySelector('[data-email-confirm-sender]');
    var fromAccount = optionLabel(form.querySelector('select[name="from_account"]'));
    senderEl.textContent = fromAccount || '(default configured sender)';

    var subjectInput = form.querySelector('input[name="subject"]');
    modalEl.querySelector('[data-email-confirm-subject]').textContent =
      cleanText(subjectInput && subjectInput.value) || '(no subject)';

    var previewEl = modalEl.querySelector('[data-email-confirm-preview]');
    var bodyHidden = form.querySelector('#id_body');
    var bodyHtml = bodyHidden ? bodyHidden.value : '';
    previewEl.innerHTML = bodyHtml
      ? bodyHtml
      : '<em class="text-muted">(no message written)</em>';
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
