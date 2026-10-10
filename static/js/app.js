(function () {
  "use strict";

  // Auto-dismiss flash messages after a few seconds.
  document.querySelectorAll(".alert-dismissible").forEach(function (alertEl) {
    window.setTimeout(function () {
      if (window.bootstrap && alertEl.isConnected) {
        window.bootstrap.Alert.getOrCreateInstance(alertEl).close();
      }
    }, 6000);
  });

  // Confirm before submitting forms that carry a data-confirm message.
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.getAttribute("data-confirm"))) {
        event.preventDefault();
      }
    });
  });

  // Fill edit modals from the data-* attributes of the button that opened them.
  document.querySelectorAll(".modal").forEach(function (modal) {
    var form = modal.querySelector("form[data-edit-form]");
    if (!form) { return; }
    modal.addEventListener("show.bs.modal", function (event) {
      var trigger = event.relatedTarget;
      if (!trigger) { return; }
      form.setAttribute("action", trigger.getAttribute("data-action"));
      form.querySelectorAll("[data-field]").forEach(function (field) {
        var value = trigger.getAttribute("data-" + field.getAttribute("data-field"));
        field.value = value === null ? "" : value;
      });
    });
  });

  // Attendance page: "mark all" buttons and a live status counter.
  var attendanceForm = document.getElementById("attendanceForm");
  if (attendanceForm) {
    var counter = document.getElementById("statusCounter");

    var updateCounter = function () {
      var counts = { Present: 0, Absent: 0, Late: 0 };
      attendanceForm.querySelectorAll(".status-radio:checked").forEach(function (radio) {
        if (Object.prototype.hasOwnProperty.call(counts, radio.value)) {
          counts[radio.value] += 1;
        }
      });
      if (counter) {
        counter.textContent = "Present: " + counts.Present + "  |  Late: " + counts.Late +
          "  |  Absent: " + counts.Absent;
      }
    };

    attendanceForm.querySelectorAll("[data-mark-all]").forEach(function (button) {
      button.addEventListener("click", function () {
        var status = button.getAttribute("data-mark-all");
        attendanceForm.querySelectorAll(".status-radio").forEach(function (radio) {
          if (radio.value === status) { radio.checked = true; }
        });
        updateCounter();
      });
    });

    attendanceForm.addEventListener("change", updateCounter);
    updateCounter();
  }
})();
