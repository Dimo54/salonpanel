document.addEventListener("DOMContentLoaded", function () {
    var csrfMeta = document.querySelector('meta[name="csrf-token"]');
    var csrfToken = csrfMeta ? csrfMeta.getAttribute("content") : "";
    if (csrfToken) {
        document.querySelectorAll('form[method="post"], form[method="POST"]').forEach(function (form) {
            if (!form.querySelector('input[name="csrf_token"]')) {
                var input = document.createElement("input");
                input.type = "hidden";
                input.name = "csrf_token";
                input.value = csrfToken;
                form.appendChild(input);
            }
        });
    }
    document.querySelectorAll("form[data-prevent-duplicate-submit]").forEach(function (form) {
        var submitted = false;
        form.addEventListener("submit", function (event) {
            if (submitted) {
                event.preventDefault();
                return;
            }
            submitted = true;
            var submitButton = event.submitter || form.querySelector('[type="submit"]');
            if (submitButton) {
                submitButton.disabled = true;
                submitButton.setAttribute("aria-busy", "true");
            }
        });
    });
    var sidebar = document.getElementById("appSidebar");
    var navToggle = document.getElementById("mobileNavToggle");
    var navClose = document.getElementById("mobileNavClose");
    var navBackdrop = document.getElementById("sidebarBackdrop");

    function setMobileNav(open) {
        if (!sidebar || !navToggle) {
            return;
        }
        sidebar.classList.toggle("is-open", open);
        document.body.classList.toggle("nav-open", open);
        navToggle.setAttribute("aria-expanded", open ? "true" : "false");
    }

    if (navToggle && sidebar) {
        navToggle.addEventListener("click", function () {
            setMobileNav(!sidebar.classList.contains("is-open"));
        });
    }
    if (navClose) {
        navClose.addEventListener("click", function () { setMobileNav(false); });
    }
    if (navBackdrop) {
        navBackdrop.addEventListener("click", function () { setMobileNav(false); });
    }
    if (sidebar) {
        sidebar.querySelectorAll("a").forEach(function (link) {
            link.addEventListener("click", function () {
                if (window.matchMedia("(max-width: 880px)").matches) {
                    setMobileNav(false);
                }
            });
        });
    }
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            setMobileNav(false);
        }
    });

    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            var message = form.getAttribute("data-confirm") || "Da li ste sigurni?";
            if (!window.confirm(message)) {
                event.preventDefault();
            }
        });
    });

    document.querySelectorAll(".auto-submit").forEach(function (input) {
        input.addEventListener("change", function () {
            if (input.form) {
                input.form.submit();
            }
        });
    });

    document.querySelectorAll(".service-assignment-checkbox").forEach(function (checkbox) {
        function syncAssignmentRow() {
            var row = checkbox.closest(".assignment-row");
            if (!row) {
                return;
            }
            row.classList.toggle("assignment-disabled", !checkbox.checked);
            row.querySelectorAll(".assignment-input").forEach(function (input) {
                input.disabled = !checkbox.checked;
            });
        }
        checkbox.addEventListener("change", syncAssignmentRow);
        syncAssignmentRow();
    });

    var appointmentForm = document.getElementById("appointmentForm");
    var serviceSelect = document.getElementById("serviceSelect");
    var workerSelect = document.getElementById("appointmentWorkerSelect");
    var priceInput = document.getElementById("priceInput");
    var durationInput = document.getElementById("appointmentDuration");
    var workerDataNode = document.getElementById("workerServiceData");

    if (appointmentForm && serviceSelect && workerSelect && workerDataNode) {
        var workerServiceData = {};
        try {
            workerServiceData = JSON.parse(workerDataNode.textContent || "{}");
        } catch (error) {
            workerServiceData = {};
        }
        var selectedWorker = String(appointmentForm.dataset.selectedWorker || "");
        var initialLoad = true;

        function selectedAssignment() {
            var rows = workerServiceData[String(serviceSelect.value)] || [];
            return rows.find(function (row) {
                return String(row.worker_id) === String(workerSelect.value);
            }) || null;
        }

        function syncAppointmentPrice() {
            var assignment = selectedAssignment();
            if (!assignment) {
                if (durationInput) {
                    durationInput.value = "-";
                }
                return;
            }
            if (durationInput) {
                durationInput.value = assignment.duration_minutes + " min";
            }
            if (priceInput && (!initialLoad || !priceInput.value)) {
                priceInput.value = Math.round(Number(assignment.price || 0));
            }
        }

        function populateAppointmentWorkers() {
            var rows = workerServiceData[String(serviceSelect.value)] || [];
            var wanted = selectedWorker || workerSelect.value;
            workerSelect.innerHTML = "";
            var placeholder = document.createElement("option");
            placeholder.value = "";
            placeholder.textContent = rows.length ? "Izaberi radnika" : "Nema radnika za ovu uslugu";
            workerSelect.appendChild(placeholder);
            rows.forEach(function (row) {
                var option = document.createElement("option");
                option.value = row.worker_id;
                option.textContent = row.worker_name + " · " + Math.round(Number(row.price || 0)).toLocaleString("sr-RS") + " RSD · " + row.duration_minutes + " min" + (row.active ? "" : " (neaktivan)");
                if (String(row.worker_id) === String(wanted)) {
                    option.selected = true;
                }
                workerSelect.appendChild(option);
            });
            workerSelect.disabled = rows.length === 0;
            if (!workerSelect.value && rows.length === 1) {
                workerSelect.value = String(rows[0].worker_id);
            }
            selectedWorker = workerSelect.value;
            syncAppointmentPrice();
            initialLoad = false;
        }

        serviceSelect.addEventListener("change", function () {
            selectedWorker = "";
            initialLoad = false;
            populateAppointmentWorkers();
        });
        workerSelect.addEventListener("change", function () {
            selectedWorker = workerSelect.value;
            initialLoad = false;
            syncAppointmentPrice();
        });
        populateAppointmentWorkers();
    }

    var allDayToggle = document.getElementById("allDayToggle");
    var partialTimeFields = document.getElementById("partialTimeFields");
    if (allDayToggle && partialTimeFields) {
        function syncTimeOffFields() {
            partialTimeFields.classList.toggle("is-hidden", allDayToggle.checked);
            partialTimeFields.querySelectorAll("input").forEach(function (input) {
                input.disabled = allDayToggle.checked;
            });
        }
        allDayToggle.addEventListener("change", syncTimeOffFields);
        syncTimeOffFields();
    }

    var startDateInput = document.getElementById("timeOffStartDate");
    var endDateInput = document.getElementById("timeOffEndDate");
    document.querySelectorAll("[data-calendar-date]").forEach(function (dayButton) {
        dayButton.addEventListener("click", function () {
            if (startDateInput) {
                startDateInput.value = dayButton.dataset.calendarDateSr || dayButton.dataset.calendarDate;
            }
            if (endDateInput) {
                endDateInput.value = dayButton.dataset.calendarDateSr || dayButton.dataset.calendarDate;
            }
            document.querySelectorAll("[data-calendar-date]").forEach(function (node) {
                node.classList.toggle("selected", node === dayButton);
            });
        });
    });

    document.querySelectorAll(".schedule-mode-select").forEach(function (select) {
        function syncScheduleRow() {
            var row = select.closest(".worker-schedule-row");
            if (!row) return;
            var custom = select.value === "custom";
            var off = select.value === "off";
            row.querySelectorAll(".schedule-custom-time").forEach(function (input) {
                input.disabled = !custom;
            });
            row.querySelectorAll(".schedule-break-time").forEach(function (input) {
                input.disabled = off;
            });
            row.classList.toggle("schedule-row-off", off);
        }
        select.addEventListener("change", syncScheduleRow);
        syncScheduleRow();
    });

    document.querySelectorAll(".day-open-toggle").forEach(function (toggle) {
        function syncDayRow() {
            var row = toggle.closest(".weekly-hours-row");
            if (!row) return;
            row.querySelectorAll(".day-time-input").forEach(function (input) {
                input.disabled = !toggle.checked;
            });
            row.classList.toggle("day-closed", !toggle.checked);
        }
        toggle.addEventListener("change", syncDayRow);
        syncDayRow();
    });
});
