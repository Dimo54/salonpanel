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
    var dateSelect = document.getElementById("bookingDate");
    var dateChoices = document.getElementById("dateChoices");
    var dateStatusNode = document.getElementById("dateAvailabilityStatus");
    var timeInput = document.getElementById("bookingTime");
    var timeSlots = document.getElementById("timeSlots");
    var statusNode = document.getElementById("timeSlotStatus");
    var summary = document.getElementById("bookingSelectionSummary");
    var priceNode = document.getElementById("bookingPrice");
    var durationNode = document.getElementById("bookingDuration");
    var selectedWorker = String(form.dataset.selectedWorker || "");
    var selectedDate = String(form.dataset.selectedDate || "");
    var selectedTime = String(form.dataset.selectedTime || timeInput.value || "");
    var availableDates = [];
    var maxDays = String(form.dataset.maxDays || "90");
    var dateRequestSequence = 0;
    var slotRequestSequence = 0;

    function formatRsd(value) {
        var amount = Number(value || 0);
        return new Intl.NumberFormat("sr-RS", { maximumFractionDigits: 0 }).format(amount) + " RSD";
    }

    function parseLocalDate(isoDate) {
        var parts = String(isoDate || "").split("-").map(Number);
        if (parts.length !== 3 || parts.some(function (part) { return !Number.isFinite(part); })) {
            return null;
        }
        return new Date(parts[0], parts[1] - 1, parts[2], 12, 0, 0, 0);
    }

    function formatDateOption(isoDate) {
        var value = parseLocalDate(isoDate);
        if (!value) {
            return isoDate;
        }
        return new Intl.DateTimeFormat("sr-Latn-RS", {
            weekday: "long",
            day: "2-digit",
            month: "long",
            year: "numeric"
        }).format(value);
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

    function setDateStatus(message, kind) {
        if (!dateStatusNode) {
            return;
        }
        dateStatusNode.textContent = message;
        dateStatusNode.className = "date-availability-status" + (kind ? " " + kind : "");
    }

    function clearDates(message) {
        availableDates = [];
        selectedDate = "";
        if (dateSelect) {
            dateSelect.innerHTML = "";
            var option = document.createElement("option");
            option.value = "";
            option.textContent = message || "Nema dostupnih datuma";
            dateSelect.appendChild(option);
            dateSelect.disabled = true;
        }
        if (dateChoices) {
            dateChoices.innerHTML = "";
        }
        clearSelectedTime();
        if (timeSlots) {
            timeSlots.innerHTML = "";
        }
    }

    function renderDateChoices() {
        if (!dateChoices || !dateSelect) {
            return;
        }
        dateChoices.innerHTML = "";
        var formatterDay = new Intl.DateTimeFormat("sr-Latn-RS", { weekday: "short" });
        var formatterDate = new Intl.DateTimeFormat("sr-Latn-RS", { day: "2-digit", month: "short" });

        availableDates.slice(0, 7).forEach(function (item, index) {
            var dateValue = parseLocalDate(item.date);
            if (!dateValue) {
                return;
            }
            var button = document.createElement("button");
            button.type = "button";
            button.className = "date-choice" + (dateSelect.value === item.date ? " active" : "");
            button.dataset.date = item.date;
            var dayLabel = index === 0 ? "Prvi slobodan" : formatterDay.format(dateValue);
            button.innerHTML = "<span>" + dayLabel + "</span><strong>" + formatterDate.format(dateValue) + "</strong><small>" + item.slots_count + " termina</small>";
            button.addEventListener("click", function () {
                dateSelect.value = this.dataset.date;
                selectedDate = dateSelect.value;
                renderDateChoices();
                clearSelectedTime();
                loadAvailability();
            });
            dateChoices.appendChild(button);
        });
    }

    function populateDateSelect(dates, keepSelection) {
        if (!dateSelect) {
            return;
        }
        var wanted = keepSelection ? String(selectedDate || dateSelect.value || "") : "";
        var exists = dates.some(function (item) { return item.date === wanted; });
        var chosen = exists ? wanted : (dates[0] ? dates[0].date : "");

        dateSelect.innerHTML = "";
        dates.forEach(function (item) {
            var option = document.createElement("option");
            option.value = item.date;
            option.textContent = formatDateOption(item.date) + " · " + item.slots_count + " slobodnih";
            option.selected = item.date === chosen;
            dateSelect.appendChild(option);
        });
        dateSelect.disabled = dates.length === 0;
        selectedDate = chosen;
        renderDateChoices();
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

    async function loadAvailableDates(keepSelection) {
        if (!serviceSelect || !workerSelect || !dateSelect) {
            return;
        }
        var serviceId = serviceSelect.value;
        var workerId = workerSelect.value;
        if (!serviceId || !workerId) {
            clearDates("Prvo izaberite uslugu i radnika");
            setDateStatus("Prvo izaberite uslugu i radnika.", "");
            setStatus("Izaberite uslugu, radnika i datum.", "");
            return;
        }

        var sequence = ++dateRequestSequence;
        dateSelect.disabled = true;
        clearSelectedTime();
        if (timeSlots) {
            timeSlots.innerHTML = "";
        }
        setDateStatus("Tražim slobodne datume...", "loading");
        setStatus("Prvo sačekajte učitavanje dostupnih datuma.", "loading");

        var params = new URLSearchParams({
            service_id: serviceId,
            worker_id: workerId,
            days: String(Number(maxDays) + 1)
        });
        try {
            var response = await fetch(form.dataset.datesUrl + "?" + params.toString(), {
                headers: { "Accept": "application/json" },
                cache: "no-store"
            });
            var payload = await response.json();
            if (sequence !== dateRequestSequence) {
                return;
            }
            if (!response.ok || !payload.ok) {
                clearDates("Datumi trenutno nisu dostupni");
                setDateStatus(payload.error || "Nije moguće učitati datume.", "empty");
                setStatus("Nema datuma za izbor.", "empty");
                return;
            }

            availableDates = payload.dates || [];
            if (!availableDates.length) {
                clearDates("Nema slobodnih datuma u narednih " + maxDays + " dana");
                setDateStatus("Radnik nema slobodan dan u narednih " + maxDays + " dana.", "empty");
                setStatus("Nema slobodnih termina.", "empty");
                return;
            }

            populateDateSelect(availableDates, keepSelection);
            setDateStatus(availableDates.length + " dostupnih datuma u narednih " + maxDays + " dana.", "ready");
            if (payload.price !== undefined && priceNode) {
                priceNode.textContent = formatRsd(payload.price);
            }
            if (payload.duration_minutes !== undefined && durationNode) {
                durationNode.textContent = payload.duration_minutes + " min";
            }
            loadAvailability();
        } catch (error) {
            if (sequence !== dateRequestSequence) {
                return;
            }
            clearDates("Datumi trenutno nisu dostupni");
            setDateStatus("Nije moguće učitati datume. Pokušajte ponovo.", "empty");
            setStatus("Nema datuma za izbor.", "empty");
        }
    }

    async function loadAvailability() {
        if (!serviceSelect || !workerSelect || !dateSelect || !timeSlots) {
            return;
        }
        var serviceId = serviceSelect.value;
        var workerId = workerSelect.value;
        var dateValue = dateSelect.value;
        timeSlots.innerHTML = "";
        if (!serviceId || !workerId || !dateValue) {
            setStatus("Izaberite uslugu, radnika i datum.", "");
            return;
        }

        var sequence = ++slotRequestSequence;
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
            if (sequence !== slotRequestSequence) {
                return;
            }
            if (!response.ok || !payload.ok) {
                renderSlots([]);
                setStatus(payload.error || "Slobodni termini trenutno nisu dostupni.", "empty");
                return;
            }
            renderSlots(payload.slots || []);
            if (payload.price !== undefined && priceNode) {
                priceNode.textContent = formatRsd(payload.price);
            }
            if (payload.duration_minutes !== undefined && durationNode) {
                durationNode.textContent = payload.duration_minutes + " min";
            }
        } catch (error) {
            if (sequence !== slotRequestSequence) {
                return;
            }
            renderSlots([]);
            setStatus("Nije moguće učitati termine. Pokušajte ponovo.", "empty");
        }
    }

    if (serviceSelect) {
        serviceSelect.addEventListener("change", function () {
            selectedWorker = "";
            selectedDate = "";
            clearSelectedTime();
            populateWorkers(false);
            loadAvailableDates(false);
        });
    }
    if (workerSelect) {
        workerSelect.addEventListener("change", function () {
            selectedWorker = workerSelect.value;
            selectedDate = "";
            clearSelectedTime();
            updateSummary();
            loadAvailableDates(false);
        });
    }
    if (dateSelect) {
        dateSelect.addEventListener("change", function () {
            selectedDate = dateSelect.value;
            renderDateChoices();
            clearSelectedTime();
            loadAvailability();
        });
    }
    form.addEventListener("submit", function (event) {
        if (!dateSelect || !dateSelect.value) {
            event.preventDefault();
            setDateStatus("Izaberite jedan od dostupnih datuma.", "empty");
            if (dateSelect) {
                dateSelect.scrollIntoView({ behavior: "smooth", block: "center" });
            }
            return;
        }
        if (!timeInput.value) {
            event.preventDefault();
            setStatus("Izaberite jedno slobodno vreme pre slanja.", "empty");
            if (timeSlots) {
                timeSlots.scrollIntoView({ behavior: "smooth", block: "center" });
            }
        }
    });

    populateWorkers(true);
    updateSummary();
    loadAvailableDates(true);
});
