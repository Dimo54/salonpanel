document.addEventListener("DOMContentLoaded", function () {
    var form = document.getElementById("bookingForm");
    var dataNode = document.getElementById("bookingData");
    if (!form || !dataNode) {
        return;
    }

    var bookingData = {};
    try {
        bookingData = JSON.parse(dataNode.textContent || "{}");
    } catch (error) {
        bookingData = {};
    }

    var serviceSelect = document.getElementById("bookingService");
    var workerSelect = document.getElementById("bookingWorker");
    var dateInput = document.getElementById("bookingDate");
    var dateChoices = document.getElementById("dateChoices");
    var timeInput = document.getElementById("bookingTime");
    var timeSlots = document.getElementById("timeSlots");
    var statusNode = document.getElementById("timeSlotStatus");
    var summary = document.getElementById("bookingSelectionSummary");
    var priceNode = document.getElementById("bookingPrice");
    var durationNode = document.getElementById("bookingDuration");
    var selectedWorker = String(form.dataset.selectedWorker || "");
    var selectedTime = String(form.dataset.selectedTime || timeInput.value || "");
    var requestSequence = 0;

    function localIsoDate(dateValue) {
        var year = dateValue.getFullYear();
        var month = String(dateValue.getMonth() + 1).padStart(2, "0");
        var day = String(dateValue.getDate()).padStart(2, "0");
        return year + "-" + month + "-" + day;
    }

    function renderDateChoices() {
        if (!dateChoices || !dateInput) {
            return;
        }
        dateChoices.innerHTML = "";
        var formatterDay = new Intl.DateTimeFormat("sr-Latn-RS", { weekday: "short" });
        var formatterDate = new Intl.DateTimeFormat("sr-Latn-RS", { day: "2-digit", month: "short" });
        var today = new Date();
        today.setHours(12, 0, 0, 0);
        for (var index = 0; index < 7; index += 1) {
            var current = new Date(today);
            current.setDate(today.getDate() + index);
            var iso = localIsoDate(current);
            var button = document.createElement("button");
            button.type = "button";
            button.className = "date-choice" + (dateInput.value === iso ? " active" : "");
            button.dataset.date = iso;
            button.innerHTML = "<span>" + (index === 0 ? "Danas" : formatterDay.format(current)) + "</span><strong>" + formatterDate.format(current) + "</strong>";
            button.addEventListener("click", function () {
                dateInput.value = this.dataset.date;
                renderDateChoices();
                clearSelectedTime();
                loadAvailability();
            });
            dateChoices.appendChild(button);
        }
    }

    function formatRsd(value) {
        var amount = Number(value || 0);
        return new Intl.NumberFormat("sr-RS", { maximumFractionDigits: 0 }).format(amount) + " RSD";
    }

    function currentAssignment() {
        var serviceId = serviceSelect ? serviceSelect.value : "";
        var workerId = workerSelect ? workerSelect.value : "";
        var rows = bookingData[String(serviceId)] || [];
        return rows.find(function (item) {
            return String(item.worker_id) === String(workerId);
        }) || null;
    }

    function updateSummary() {
        var assignment = currentAssignment();
        if (!summary || !priceNode || !durationNode) {
            return;
        }
        if (!assignment) {
            summary.hidden = true;
            return;
        }
        priceNode.textContent = formatRsd(assignment.price);
        durationNode.textContent = assignment.duration_minutes + " min";
        summary.hidden = false;
    }

    function populateWorkers(keepSelection) {
        if (!serviceSelect || !workerSelect) {
            return;
        }
        var serviceId = serviceSelect.value;
        var rows = bookingData[String(serviceId)] || [];
        var wanted = keepSelection ? String(selectedWorker || workerSelect.value || "") : "";
        workerSelect.innerHTML = "";

        var placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = rows.length ? "Izaberite radnika" : "Nema dostupnih radnika";
        workerSelect.appendChild(placeholder);

        rows.forEach(function (item) {
            var option = document.createElement("option");
            option.value = item.worker_id;
            option.textContent = item.worker_name + " · " + formatRsd(item.price) + " · " + item.duration_minutes + " min";
            if (String(item.worker_id) === wanted) {
                option.selected = true;
            }
            workerSelect.appendChild(option);
        });
        workerSelect.disabled = rows.length === 0;
        if (!workerSelect.value && rows.length === 1) {
            workerSelect.value = String(rows[0].worker_id);
        }
        selectedWorker = workerSelect.value;
        updateSummary();
    }

    function clearSelectedTime() {
        selectedTime = "";
        if (timeInput) {
            timeInput.value = "";
        }
        if (timeSlots) {
            timeSlots.querySelectorAll(".time-slot.active").forEach(function (node) {
                node.classList.remove("active");
            });
        }
    }

    function setStatus(message, kind) {
        if (!statusNode) {
            return;
        }
        statusNode.textContent = message;
        statusNode.className = "time-slot-status" + (kind ? " " + kind : "");
    }

    function renderSlots(slots) {
        if (!timeSlots || !timeInput) {
            return;
        }
        timeSlots.innerHTML = "";
        if (!slots.length) {
            clearSelectedTime();
            setStatus("Nema slobodnih termina za izabrani datum.", "empty");
            return;
        }

        var selectedStillExists = slots.indexOf(selectedTime) !== -1;
        if (!selectedStillExists) {
            selectedTime = "";
            timeInput.value = "";
        }
        slots.forEach(function (slot) {
            var button = document.createElement("button");
            button.type = "button";
            button.className = "time-slot" + (slot === selectedTime ? " active" : "");
            button.textContent = slot;
            button.dataset.time = slot;
            button.addEventListener("click", function () {
                selectedTime = this.dataset.time;
                timeInput.value = selectedTime;
                timeSlots.querySelectorAll(".time-slot").forEach(function (node) {
                    node.classList.toggle("active", node.dataset.time === selectedTime);
                });
                setStatus("Izabrano vreme: " + selectedTime, "selected");
            });
            timeSlots.appendChild(button);
        });
        if (selectedStillExists) {
            timeInput.value = selectedTime;
            setStatus("Izabrano vreme: " + selectedTime, "selected");
        } else {
            setStatus(slots.length + " slobodnih termina", "ready");
        }
    }

    async function loadAvailability() {
        if (!serviceSelect || !workerSelect || !dateInput || !timeSlots) {
            return;
        }
        var serviceId = serviceSelect.value;
        var workerId = workerSelect.value;
        var dateValue = dateInput.value;
        timeSlots.innerHTML = "";
        if (!serviceId || !workerId || !dateValue) {
            setStatus("Izaberite uslugu, radnika i datum.", "");
            return;
        }

        var sequence = ++requestSequence;
        setStatus("Učitavanje slobodnih termina...", "loading");
        var params = new URLSearchParams({
            service_id: serviceId,
            worker_id: workerId,
            date: dateValue
        });
        try {
            var response = await fetch(form.dataset.availabilityUrl + "?" + params.toString(), {
                headers: { "Accept": "application/json" },
                cache: "no-store"
            });
            var payload = await response.json();
            if (sequence !== requestSequence) {
                return;
            }
            if (!response.ok || !payload.ok) {
                renderSlots([]);
                setStatus(payload.error || "Slobodni termini trenutno nisu dostupni.", "empty");
                return;
            }
            renderSlots(payload.slots || []);
            if (payload.price !== undefined) {
                priceNode.textContent = formatRsd(payload.price);
            }
            if (payload.duration_minutes !== undefined) {
                durationNode.textContent = payload.duration_minutes + " min";
            }
        } catch (error) {
            if (sequence !== requestSequence) {
                return;
            }
            renderSlots([]);
            setStatus("Nije moguće učitati termine. Pokušajte ponovo.", "empty");
        }
    }

    if (serviceSelect) {
        serviceSelect.addEventListener("change", function () {
            selectedWorker = "";
            clearSelectedTime();
            populateWorkers(false);
            loadAvailability();
        });
    }
    if (workerSelect) {
        workerSelect.addEventListener("change", function () {
            selectedWorker = workerSelect.value;
            clearSelectedTime();
            updateSummary();
            loadAvailability();
        });
    }
    if (dateInput) {
        dateInput.addEventListener("change", function () {
            renderDateChoices();
            clearSelectedTime();
            loadAvailability();
        });
    }
    form.addEventListener("submit", function (event) {
        if (!timeInput.value) {
            event.preventDefault();
            setStatus("Izaberite jedno slobodno vreme pre slanja.", "empty");
            if (timeSlots) {
                timeSlots.scrollIntoView({ behavior: "smooth", block: "center" });
            }
        }
    });

    renderDateChoices();
    populateWorkers(true);
    updateSummary();
    loadAvailability();
});
